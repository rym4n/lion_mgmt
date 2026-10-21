"""Tenant-scoped read models and presentation mutations for topology graph/v2."""

from __future__ import annotations

from base64 import urlsafe_b64decode
from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Protocol

from tortoise.transactions import in_transaction

from models import (
    NetworkTopology,
    NetworkTopologyVersion,
    PresentationOverride,
    TopologyLayoutSet,
    TopologyProfileRevision,
)
from services.network_topology.canonicalization import canonicalize_json, sha256_uri
from services.network_topology.contracts import validate_contract
from services.network_topology.layouts import (
    LayoutError,
    decode_scope_version_map,
    encode_json,
    validate_override,
)
from services.network_topology.overflow import (
    DIRECT_ITEM_LIMIT,
    OverflowTokenError,
    page_bucket,
)
from services.network_topology.projection import project_scope, search_canonical
from services.network_topology.relationships import RelationshipResult
from services.network_topology.scope_tree import ScopeTreeResult


VIEW_REQUEST = "aioe.network-topology.api/view-request/v1"
VIEW_RESPONSE = "aioe.network-topology.api/view-response/v1"
PROFILE_REQUEST = "aioe.network-topology.api/profile-request/v1"
PROFILE_RESPONSE = "aioe.network-topology.api/profile-response/v1"
GRAPH_SCHEMA = "aioe.network-topology.graph/v2"
SEGMENT_KINDS = frozenset({"network_segment", "segment", "subnet"})
PROVENANCE_FIELDS = {
    "observed": frozenset(
        {"type", "resource_id", "observation_id", "redacted_sha256", "locator"}
    ),
    "authoritative": frozenset(
        {
            "type",
            "source_system",
            "source_record_id",
            "source_revision",
            "as_of",
            "field_locator",
        }
    ),
    "derived": frozenset(
        {"type", "rule_id", "profile_revision", "member_ids", "manual_override_ref"}
    ),
}


class QueryError(RuntimeError):
    def __init__(self, message: str, status: int = 400) -> None:
        self.message = message
        self.status = status
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class QueryAuthority:
    tenant_id: str
    topology_id: str
    actor: str


@dataclass(frozen=True, slots=True)
class QueryDocument:
    topology_id: str
    graph_version_id: str
    graph_revision: int
    layout_set_id: str | None
    layout_set_revision: int
    scope_layout_revisions: Mapping[str, int]
    graph: Mapping[str, Any]


class QueryRepository(Protocol):
    async def load_document(self, authority, version_id): ...


class QueryCache(Protocol):
    async def get(self, key): ...
    async def set(self, key, value): ...


class MemoryQueryCache:
    """Small immutable-version cache; keys include every presentation pointer."""

    def __init__(self, limit: int = 256) -> None:
        self.limit = limit
        self.values: OrderedDict[str, dict[str, Any]] = OrderedDict()

    async def get(self, key):
        value = self.values.get(key)
        if value is None:
            return None
        self.values.move_to_end(key)
        return deepcopy(value)

    async def set(self, key, value):
        self.values[key] = deepcopy(value)
        self.values.move_to_end(key)
        while len(self.values) > self.limit:
            self.values.popitem(last=False)


QUERY_CACHE = MemoryQueryCache()


class OrmQueryRepository:
    async def load_document(self, authority, version_id):
        topology = await NetworkTopology.filter(
            id=authority.topology_id,
            tenant_id=authority.tenant_id,
            deleted=False,
        ).first()
        if topology is None:
            raise QueryError("拓扑数据不存在", 404)
        version = await self._version(authority, topology, version_id)
        graph = version.topology_json if version is not None else topology.topology_json
        if not isinstance(graph, Mapping) or graph.get("schema_version") != GRAPH_SCHEMA:
            raise QueryError("拓扑版本不是 graph/v2", 409)
        graph_version_id = self._graph_version_id(topology, version)
        graph_revision = self._graph_revision(topology, version)
        layout_id = self._layout_id(topology, version)
        layout = await self._layout(authority, layout_id)
        return QueryDocument(
            topology.id,
            graph_version_id,
            graph_revision,
            layout_id,
            int(getattr(layout, "layout_set_revision", 0) or 0),
            self._scope_revisions(layout),
            deepcopy(graph),
        )

    async def _version(self, authority, topology, version_id):
        if version_id is None:
            return None
        version = await NetworkTopologyVersion.filter(
            id=version_id,
            topology_id=topology.id,
            tenant_id=authority.tenant_id,
            deleted=False,
        ).first()
        if version is None:
            raise QueryError("拓扑版本不存在", 404)
        return version

    def _graph_version_id(self, topology, version):
        value = getattr(version, "id", None)
        if value is None:
            value = getattr(topology, "current_graph_version_row_id", None)
        if value is None:
            raise QueryError("拓扑图版本不存在", 409)
        return str(value)

    def _graph_revision(self, topology, version):
        if version is not None:
            return int(version.version)
        return int(topology.current_graph_revision or 0)

    def _layout_id(self, topology, version):
        if version is not None:
            return getattr(version, "published_layout_set_row_id", None)
        return getattr(topology, "current_layout_set_row_id", None)

    async def _layout(self, authority, layout_id):
        if layout_id is None:
            return None
        layout = await TopologyLayoutSet.filter(
            id=layout_id,
            tenant_id=authority.tenant_id,
            topology_row_id=authority.topology_id,
            deleted=False,
        ).first()
        if layout is None:
            raise QueryError("拓扑布局集合不存在", 409)
        return layout

    def _scope_revisions(self, layout):
        if layout is None:
            return {}
        mapping = decode_scope_version_map(layout.scope_version_map_json)
        return {
            scope_id: int(entry.get("scope_layout_revision", 0) or 0)
            for scope_id, entry in mapping.items()
        }


def build_query_cache_key(
    *, tenant_id, topology_id, graph_version_id, scope_id, layers, bucket,
    layout_set_revision,
):
    values = (
        "network-topology-view-v1",
        tenant_id,
        topology_id,
        graph_version_id,
        scope_id,
        ",".join(sorted(layers)),
        bucket or "-",
        str(layout_set_revision),
    )
    return ":".join(values)


async def scoped_view(authority, payload, *, repository=None, cache=None):
    validate_contract(payload, VIEW_REQUEST)
    _require_authority(authority, payload["id"])
    repo = repository or OrmQueryRepository()
    document = await repo.load_document(authority, payload["version_id"])
    bucket = _token_bucket(payload["continuation_token"])
    key = build_query_cache_key(
        tenant_id=authority.tenant_id,
        topology_id=authority.topology_id,
        graph_version_id=document.graph_version_id,
        scope_id=payload["scope_id"],
        layers=tuple(payload["layers"]),
        bucket=_cache_bucket(bucket, payload["continuation_token"]),
        layout_set_revision=document.layout_set_revision,
    )
    store = cache or QUERY_CACHE
    cached = await store.get(key)
    if cached is not None:
        return cached
    data = _project_document(document, payload, bucket)
    validate_contract({"code": 0, "data": data}, VIEW_RESPONSE)
    await store.set(key, data)
    return data


def _project_document(document, payload, bucket):
    tree, relations = _graph_inputs(document.graph)
    projection = project_scope(
        tree, relations, payload["scope_id"], layers=payload["layers"]
    )
    nodes = projection.nodes
    edges = projection.edges
    continuation = None
    if payload["continuation_token"] is not None:
        nodes, edges, continuation = _bucket_page(
            tree, relations, payload, bucket, projection.direct_item_total
        )
    data = {
        "current_scope": _api_scope(projection.current_scope),
        "breadcrumbs": [
            _api_breadcrumb(item) for item in projection.breadcrumbs[:-1]
        ],
        "nodes": [_api_node(item) for item in nodes],
        "edges": [_api_edge(item) for item in edges],
        "scope_layout_revision": int(
            document.scope_layout_revisions.get(payload["scope_id"], 0)
        ),
        "layout_set_revision": document.layout_set_revision,
        "graph_revision": document.graph_revision,
        "total_direct_items": projection.direct_item_total,
        "overflow_strategy": "deterministic_bucket",
        "view_buckets": [str(item["id"]) for item in projection.buckets],
        "continuation_token": continuation,
    }
    return data


def _bucket_page(tree, relations, payload, bucket, total):
    full = project_scope(
        tree,
        relations,
        payload["scope_id"],
        layers=payload["layers"],
        limit=max(total, DIRECT_ITEM_LIMIT) + 1,
    )
    page = page_bucket(
        payload["scope_id"],
        full.nodes,
        bucket,
        payload["continuation_token"],
    )
    visible = {item["id"] for item in page.items}
    edges = tuple(
        edge for edge in full.edges if _edge_endpoints(edge).issubset(visible)
    )
    return page.items, edges, page.continuation_token


def _graph_inputs(graph):
    tree = ScopeTreeResult(
        str(graph["root_scope_id"]),
        tuple(deepcopy(graph.get("scopes", ()))),
        tuple(deepcopy(graph.get("entities", ()))),
        (),
        (),
        (),
    )
    relations = RelationshipResult(tuple(deepcopy(graph.get("relations", ()))), ())
    return tree, relations


def _api_scope(value):
    return {key: str(value[key]) for key in ("id", "name", "scope_type")}


def _api_breadcrumb(value):
    return {key: str(value[key]) for key in ("id", "name")}


def _api_node(value):
    kind = str(value.get("projection_kind") or "entity")
    projection_type = {
        "view_bucket": "bucket",
        "entity_ref": "external_ref" if value.get("ref_reason") == "external" else "entity_ref",
    }.get(kind, kind)
    target = value.get("ref_id") or value.get("id")
    if kind == "view_bucket":
        target = value.get("continuation_token") or value["id"]
    node = {
        "id": str(value["id"]),
        "projection_type": projection_type,
        "label": str(value.get("name") or value.get("category") or value["id"]),
        "read_only": True,
        "target_id": str(target),
    }
    if value.get("kind") in {"device", "network_segment", "external", "service_boundary"}:
        node["entity_kind"] = value["kind"]
    return node


def _api_edge(value):
    endpoints = [str(item["ref_id"]) for item in value["endpoints"]]
    members = [str(item) for item in value.get("member_relation_ids", ())]
    return {
        "id": str(value["id"]),
        "source": endpoints[0],
        "target": endpoints[1],
        "relation_type": str(value["relation_type"]),
        "layer": str(value["layer"]),
        "member_relation_ids": list(dict.fromkeys(members)),
    }


def _edge_endpoints(edge):
    return {str(item["ref_id"]) for item in edge.get("endpoints", ())}


def _token_bucket(token):
    if token is None:
        return None
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(urlsafe_b64decode(token + padding))
        bucket = payload["bucket"]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise OverflowTokenError("invalid overflow continuation token") from exc
    if not isinstance(bucket, str) or not bucket:
        raise OverflowTokenError("invalid overflow continuation token")
    return bucket


def _cache_bucket(bucket, token):
    if bucket is None:
        return None
    return f"{bucket}@{sha256_uri(token.encode())}"


async def search_graph(authority, payload, *, repository=None):
    _validate_simple_request(payload, required={"id", "query", "version_id"})
    _require_authority(authority, payload["id"])
    query = payload["query"]
    if not isinstance(query, str) or not query.strip():
        raise QueryError("搜索关键词不能为空")
    document = await (repository or OrmQueryRepository()).load_document(
        authority, payload["version_id"]
    )
    tree, relations = _graph_inputs(document.graph)
    return {
        "graph_version_id": document.graph_version_id,
        "items": list(search_canonical(tree, query, relations)),
    }


async def evidence_query(authority, payload, *, repository=None):
    _validate_simple_request(payload, required={"id", "target_id", "version_id"})
    _require_authority(authority, payload["id"])
    target_id = payload["target_id"]
    if not isinstance(target_id, str) or not target_id:
        raise QueryError("证据目标不能为空")
    document = await (repository or OrmQueryRepository()).load_document(
        authority, payload["version_id"]
    )
    target = _graph_item(document.graph, target_id)
    if target is None:
        raise QueryError("证据目标不存在", 404)
    return {
        "graph_version_id": document.graph_version_id,
        "target_id": target_id,
        "provenance": [_safe_provenance(item) for item in target.get("provenance", ())],
    }


def _graph_item(graph, target_id):
    for collection in ("scopes", "entities", "relations"):
        for item in graph.get(collection, ()):
            if item.get("id") == target_id:
                return item
    return None


def _safe_provenance(value):
    allowed = PROVENANCE_FIELDS.get(value.get("type"), frozenset({"type"}))
    return {key: deepcopy(value[key]) for key in allowed if key in value}


def graph_statistics(graph):
    document = graph if isinstance(graph, Mapping) else {}
    entities = document.get("entities", ())
    return {
        "node_count": len(entities),
        "edge_count": len(document.get("relations", ())),
        "scope_count": len(document.get("scopes", ())),
        "key_device_count": sum(
            item.get("importance") in {"critical", "important"} for item in entities
        ),
        "segment_count": sum(item.get("kind") in SEGMENT_KINDS for item in entities),
        "relation_count": len(document.get("relations", ())),
    }


def compatible_root_graph(graph):
    tree, relations = _graph_inputs(graph)
    projection = project_scope(tree, relations, tree.root_scope_id)
    return {
        "nodes": [_api_node(item) for item in projection.nodes],
        "edges": [_api_edge(item) for item in projection.edges],
        "read_only": True,
    }


def enrich_v2_payload(payload, graph):
    compatible = compatible_root_graph(graph)
    return {
        **payload,
        "topology_json": compatible,
        **graph_statistics(graph),
    }


def _validate_simple_request(payload, *, required):
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise QueryError("请求参数不合法")
    topology_id = payload.get("id")
    version_id = payload.get("version_id")
    if not isinstance(topology_id, str) or not topology_id:
        raise QueryError("拓扑ID不能为空")
    if version_id is not None and (not isinstance(version_id, str) or not version_id):
        raise QueryError("拓扑版本ID不合法")


def _require_authority(authority, topology_id):
    if not isinstance(authority, QueryAuthority):
        raise QueryError("查询权限上下文不合法")
    if authority.topology_id != topology_id:
        raise QueryError("拓扑ID与权限上下文不一致")
    if not authority.tenant_id:
        raise QueryError("租户不存在")


async def save_profile(authority, payload):
    validate_contract(payload, PROFILE_REQUEST)
    _require_authority(authority, payload["id"])
    canonical = canonicalize_json(payload["profile"])
    async with in_transaction() as connection:
        topology = await _locked_topology(authority, connection)
        expected = payload["expected_resource_revision"]
        if int(topology.resource_revision or 0) != expected:
            raise QueryError("拓扑资源版本已更新", 409)
        profile_revision = int(topology.topology_profile_revision or 0) + 1
        resource_revision = expected + 1
        await TopologyProfileRevision.create(
            using_db=connection,
            tenant_id=authority.tenant_id,
            topology_row_id=authority.topology_id,
            revision=profile_revision,
            resource_revision=resource_revision,
            profile_json=canonical.decode(),
            profile_hash=sha256_uri(canonical),
            created_by=authority.actor,
        )
        topology.topology_profile_revision = profile_revision
        topology.resource_revision = resource_revision
        await topology.save(
            using_db=connection,
            update_fields=["topology_profile_revision", "resource_revision", "utime"],
        )
    result = {
        "profile_revision": profile_revision,
        "resource_revision": resource_revision,
    }
    validate_contract({"code": 0, "data": result}, PROFILE_RESPONSE)
    return result


async def save_override(authority, payload):
    required = {
        "id", "target_id", "profile_revision", "override_kind", "override_value"
    }
    _validate_simple_override(payload, required)
    _require_authority(authority, payload["id"])
    async with in_transaction() as connection:
        topology = await _locked_topology(authority, connection)
        graph = topology.topology_json or {}
        target = _graph_item(graph, payload["target_id"])
        if target is None:
            raise QueryError("展示覆盖目标不存在", 404)
        scopes = {item["id"] for item in graph.get("scopes", ())}
        try:
            validate_override(
                payload["override_kind"], payload["override_value"], scopes
            )
        except LayoutError as exc:
            raise QueryError(exc.public_message, exc.status) from exc
        profile = await TopologyProfileRevision.filter(
            tenant_id=authority.tenant_id,
            topology_row_id=authority.topology_id,
            revision=payload["profile_revision"],
            deleted=False,
        ).using_db(connection).first()
        if profile is None:
            raise QueryError("Profile 版本不存在", 404)
        row = await PresentationOverride.create(
            using_db=connection,
            tenant_id=authority.tenant_id,
            topology_row_id=authority.topology_id,
            profile_revision_row_id=profile.id,
            target_stable_id=payload["target_id"],
            override_kind=payload["override_kind"],
            override_value=encode_json(payload["override_value"]),
            status="ACTIVE",
            created_by=authority.actor,
        )
    return {"id": str(row.id), "status": "ACTIVE"}


def _validate_simple_override(payload, required):
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise QueryError("展示覆盖请求不合法")
    for key in ("id", "target_id", "override_kind"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise QueryError("展示覆盖请求不合法")
    revision = payload["profile_revision"]
    if type(revision) is not int or revision < 1:
        raise QueryError("Profile 版本不合法")


async def _locked_topology(authority, connection):
    topology = await NetworkTopology.filter(
        id=authority.topology_id,
        tenant_id=authority.tenant_id,
        deleted=False,
    ).using_db(connection).select_for_update().first()
    if topology is None:
        raise QueryError("拓扑数据不存在", 404)
    return topology
