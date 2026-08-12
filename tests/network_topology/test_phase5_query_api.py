"""Phase 5 scoped query API contract and domain tests."""

from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.network_topology.contracts import ContractError, load_schema, validate_contract
from services.network_topology.query_service import (
    QueryAuthority,
    QueryDocument,
    build_query_cache_key,
    compatible_root_graph,
    evidence_query,
    graph_statistics,
    save_override,
    save_profile,
    scoped_view,
    search_graph,
)


ROOT = Path(__file__).resolve().parents[2]
VIEW_RESPONSE = "aioe.network-topology.api/view-response/v1"


def _graph(entity_count=2):
    entities = _entities(entity_count)
    return {
        "schema_version": "aioe.network-topology.graph/v2",
        "root_scope_id": "scope:organization:root",
        "scopes": _scopes(),
        "entities": entities,
        "relations": _relations(entities),
    }


def _entities(entity_count):
    return [
        {
            "id": f"entity:router-{index}",
            "kind": "device",
            "scope_id": "scope:site:hq",
            "scope_memberships": [],
            "name": f"Router {index}",
            "role": "router",
            "importance": "important",
            "provenance": [
                {
                    "type": "derived",
                    "rule_id": "importance/v1",
                    "profile_revision": 1,
                    "member_ids": [f"device-{index}"],
                    "password": "must-not-leak",
                }
            ],
        }
        for index in range(entity_count)
    ]


def _scopes():
    return [
        {
            "id": "scope:organization:root",
            "parent_scope_id": None,
            "scope_type": "organization",
            "name": "Synthetic Organization",
            "summary": {},
            "provenance": [],
        },
        {
            "id": "scope:site:hq",
            "parent_scope_id": "scope:organization:root",
            "scope_type": "site",
            "name": "HQ",
            "summary": {},
            "provenance": [],
        },
    ]


def _relations(entities):
    relations = []
    if len(entities) >= 2:
        relations.append(
            {
                "id": "relation:uplink",
                "relation_type": "l3_connectivity",
                "layer": "l3",
                "endpoints": [
                    {"ref_type": "entity", "ref_id": entities[0]["id"]},
                    {"ref_type": "entity", "ref_id": entities[1]["id"]},
                ],
                "provenance": [
                    {
                        "type": "observed",
                        "observation_id": "observation-1",
                        "locator": {"kind": "json_pointer", "value": "/neighbors/0"},
                        "password": "must-not-leak",
                    }
                ],
            }
        )
    return relations


def _document(*, graph=None, version_id="version-7", layout_revision=4):
    return QueryDocument(
        topology_id="topology-1",
        graph_version_id=version_id,
        graph_revision=7,
        layout_set_id="layout-set-4",
        layout_set_revision=layout_revision,
        scope_layout_revisions={"scope:site:hq": 3},
        graph=graph or _graph(),
    )


class MemoryRepository:
    def __init__(self, document=None):
        self.document = document or _document()
        self.calls = []

    async def load_document(self, authority, version_id):
        self.calls.append((authority, version_id))
        return deepcopy(self.document)


class MemoryCache:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        value = self.values.get(key)
        return deepcopy(value)

    async def set(self, key, value):
        self.values[key] = deepcopy(value)


def _authority(tenant_id="tenant-1"):
    return QueryAuthority(tenant_id, "topology-1", "account-1")


def _request(scope_id="scope:site:hq", continuation_token=None):
    return {
        "id": "topology-1",
        "scope_id": scope_id,
        "version_id": None,
        "layers": ["l3"],
        "continuation_token": continuation_token,
    }


class MemoryTransaction:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_view_matches_frozen_schema_and_uses_tenant_scoped_repository():
    repository = MemoryRepository()

    data = await scoped_view(_authority(), _request(), repository=repository)

    response = {"code": 0, "data": data}
    validate_contract(response, VIEW_RESPONSE)
    assert set(data) == {
        "current_scope",
        "breadcrumbs",
        "nodes",
        "edges",
        "scope_layout_revision",
        "layout_set_revision",
        "graph_revision",
        "total_direct_items",
        "overflow_strategy",
        "view_buckets",
        "continuation_token",
    }
    assert repository.calls == [(_authority(), None)]
    assert data["scope_layout_revision"] == 3
    assert data["layout_set_revision"] == 4
    assert data["nodes"][0] == {
        "id": "entity:router-0",
        "projection_type": "entity",
        "label": "Router 0",
        "read_only": True,
        "target_id": "entity:router-0",
    }


@pytest.mark.asyncio
async def test_historical_view_uses_versions_published_layout_set():
    repository = MemoryRepository(_document(version_id="version-old", layout_revision=2))
    payload = _request()
    payload["version_id"] = "version-old"

    data = await scoped_view(_authority(), payload, repository=repository)

    assert repository.calls == [(_authority(), "version-old")]
    assert data["graph_revision"] == 7
    assert data["layout_set_revision"] == 2


@pytest.mark.asyncio
async def test_view_continuation_pages_a_stable_bucket():
    repository = MemoryRepository(
        _document(graph=_graph(205), version_id="version-205")
    )
    first = await scoped_view(_authority(), _request(), repository=repository)
    assert first["total_direct_items"] == 205
    assert len(first["nodes"]) == 1
    assert first["nodes"][0]["projection_type"] == "bucket"
    assert first["view_buckets"] == [first["nodes"][0]["id"]]

    token = first["nodes"][0]["target_id"]
    page = await scoped_view(
        _authority(), _request(continuation_token=token), repository=repository
    )
    assert len(page["nodes"]) == 200
    assert page["continuation_token"] is not None
    validate_contract({"code": 0, "data": page}, VIEW_RESPONSE)
    final_page = await scoped_view(
        _authority(),
        _request(continuation_token=page["continuation_token"]),
        repository=repository,
    )
    assert len(final_page["nodes"]) == 5
    assert final_page["continuation_token"] is None


@pytest.mark.asyncio
async def test_view_rejects_non_frozen_request_before_repository_access():
    repository = MemoryRepository()
    payload = _request()
    payload["bucket_id"] = "not-in-frozen-schema"

    with pytest.raises(ContractError):
        await scoped_view(_authority(), payload, repository=repository)

    assert repository.calls == []


@pytest.mark.asyncio
async def test_cache_key_covers_every_frozen_query_dimension():
    repository = MemoryRepository()
    cache = MemoryCache()

    await scoped_view(_authority(), _request(), repository=repository, cache=cache)
    await scoped_view(_authority(), _request(), repository=repository, cache=cache)

    assert len(repository.calls) == 2
    assert len(cache.values) == 1
    key = next(iter(cache.values))
    assert key == build_query_cache_key(
        tenant_id="tenant-1",
        topology_id="topology-1",
        graph_version_id="version-7",
        scope_id="scope:site:hq",
        layers=("l3",),
        bucket=None,
        layout_set_revision=4,
    )


@pytest.mark.asyncio
async def test_search_is_global_and_evidence_is_redacted():
    repository = MemoryRepository(_document(graph=_graph(205)))

    matches = await search_graph(
        _authority(), {"id": "topology-1", "query": "Router 204", "version_id": None},
        repository=repository,
    )
    evidence = await evidence_query(
        _authority(),
        {"id": "topology-1", "target_id": "relation:uplink", "version_id": None},
        repository=repository,
    )

    assert matches["items"][0]["canonical_id"] == "entity:router-204"
    assert matches["items"][0]["bucket_id"] is not None
    serialized = json.dumps(evidence)
    assert "observation-1" in serialized
    assert "password" not in serialized
    assert "must-not-leak" not in serialized
    assert "facts" not in evidence


def test_v2_statistics_and_root_compatibility_keep_legacy_shape_read_only():
    graph = _graph()
    stats = graph_statistics(graph)
    compatible = compatible_root_graph(graph)

    assert stats == {
        "scope_count": 2,
        "key_device_count": 2,
        "segment_count": 0,
        "relation_count": 1,
    }
    assert set(compatible) == {"nodes", "edges", "read_only"}
    assert compatible["read_only"] is True
    assert all(set(node) == {"id", "projection_type", "label", "read_only", "target_id"} for node in compatible["nodes"])


@pytest.mark.asyncio
async def test_profile_save_uses_resource_cas_and_creates_immutable_revision(monkeypatch):
    from services.network_topology import query_service

    payload = load_schema(query_service.PROFILE_REQUEST)["examples"][0]
    topology = SimpleNamespace(
        resource_revision=1,
        topology_profile_revision=2,
        saved=[],
    )

    async def save(**kwargs):
        topology.saved.append(kwargs)

    async def locked(_authority, _connection):
        return topology

    created = []

    class FakeProfiles:
        @classmethod
        async def create(cls, **kwargs):
            created.append(kwargs)

    topology.save = save
    monkeypatch.setattr(query_service, "in_transaction", MemoryTransaction)
    monkeypatch.setattr(query_service, "_locked_topology", locked)
    monkeypatch.setattr(query_service, "TopologyProfileRevision", FakeProfiles)

    result = await save_profile(_authority(), payload)

    assert result == {"profile_revision": 3, "resource_revision": 2}
    assert created[0]["revision"] == 3
    assert created[0]["resource_revision"] == 2
    assert created[0]["profile_hash"].startswith("sha256:")
    assert topology.saved[0]["update_fields"] == [
        "topology_profile_revision",
        "resource_revision",
        "utime",
    ]


@pytest.mark.asyncio
async def test_override_rejects_unauthorized_scope_mapping(monkeypatch):
    from services.network_topology import query_service

    topology = SimpleNamespace(topology_json=_graph())

    async def locked(_authority, _connection):
        return topology

    monkeypatch.setattr(query_service, "in_transaction", MemoryTransaction)
    monkeypatch.setattr(query_service, "_locked_topology", locked)
    payload = {
        "id": "topology-1",
        "target_id": "entity:router-0",
        "profile_revision": 1,
        "override_kind": "scope_mapping",
        "override_value": {"scope_ids": ["scope:site:other-tenant"]},
    }

    with pytest.raises(query_service.QueryError) as error:
        await save_override(_authority(), payload)

    assert error.value.status == 403


def test_new_routes_are_registered_with_exact_permission_matrix():
    source = (ROOT / "services/network_topology/views.py").read_text()
    contracts = {}
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route = None
        permissions = ()
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "post":
                route = decorator.args[0].value
            if isinstance(decorator.func, ast.Name) and decorator.func.id == "requires_permission":
                permissions = tuple(item.value for item in decorator.args)
        if route:
            contracts[route] = (node.name, permissions)
    additions = {
        path: contract
        for path, contract in contracts.items()
        if path in {"/view", "/search", "/evidence", "/profile", "/override"}
    }
    assert additions == {
        "/view": ("view_topology_scope", ("network_topology:r",)),
        "/search": ("search_topology", ("network_topology:r",)),
        "/evidence": ("get_topology_evidence", ("network_topology:r",)),
        "/profile": ("save_topology_profile", ("network_topology:profile_admin",)),
        "/override": ("save_topology_override", ("network_topology:u",)),
    }


@pytest.mark.asyncio
async def test_kill_switch_is_dynamic_for_view_search_and_evidence(monkeypatch):
    from services.network_topology import query_views

    monkeypatch.setattr(query_views.settings, "TOPOLOGY_V2_VIEW_ENABLED", False)
    request = SimpleNamespace(json={}, ctx=SimpleNamespace(session={}))

    responses = [
        await query_views.view_topology_scope(request),
        await query_views.search_topology(request),
        await query_views.get_topology_evidence(request),
    ]

    assert [response.status for response in responses] == [503, 503, 503]
    source = (ROOT / "services/network_topology/query_views.py").read_text()
    assert "import settings" in source
    assert "from settings import" not in source


@pytest.mark.asyncio
async def test_layout_adapter_validates_frozen_request_and_response(monkeypatch):
    from services.network_topology import mutation_views

    payload = {
        "id": "topology-1",
        "scope_id": "scope:site:hq",
        "expected_scope_layout_revision": 2,
        "graph_revision": 7,
        "items": {"entity:router-0": {"x": 1, "y": 2, "pinned": True}},
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "algorithm": "manual",
    }
    captured = {}

    async def fake_save(authority, data):
        captured["authority"] = authority
        captured["data"] = data
        return SimpleNamespace(
            scope_layout_revision=3,
            layout_set_revision=5,
            graph_revision=7,
        )

    monkeypatch.setattr(
        mutation_views.settings, "TOPOLOGY_V2_PRESENTATION_WRITE_ENABLED", True
    )
    monkeypatch.setattr(mutation_views, "save_scope_layout", fake_save)
    request = SimpleNamespace(
        json=payload,
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "sub": "account-1"}),
    )

    response = await mutation_views.save_topology_layout(request)
    body = json.loads(response.body)

    validate_contract(body, "aioe.network-topology.api/layout-response/v2")
    assert captured["authority"].tenant_id == "tenant-1"
    assert captured["data"] == payload
