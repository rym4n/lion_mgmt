"""Scope-local presentation projections over canonical topology graphs."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping

from services.network_topology.overflow import (
    DIRECT_ITEM_LIMIT,
    locate_item_bucket,
    plan_overflow,
)
from services.network_topology.relationships import RelationshipResult
from services.network_topology.scope_tree import ScopeTreeResult


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    current_scope: Mapping[str, Any]
    breadcrumbs: tuple[dict[str, Any], ...]
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    direct_item_total: int
    overflow: Mapping[str, Any] | None
    buckets: tuple[dict[str, Any], ...]


def project_scope(
    scope_tree: ScopeTreeResult,
    relationships: RelationshipResult,
    scope_id: str,
    *,
    limit: int = DIRECT_ITEM_LIMIT,
    layers: Iterable[str] | None = None,
    include_configured_segments: bool = False,
    preserve_parallel: bool = False,
) -> ProjectionResult:
    """Build a drill-down view without adding objects to the canonical graph."""
    state = _ProjectionState(scope_tree, relationships, scope_id, layers, include_configured_segments)
    items, edge_specs = state.project_items_and_relations()
    overflow_plan = plan_overflow(scope_id, items, limit=limit)
    nodes, buckets, member_map = _visible_nodes(overflow_plan)
    edges = _aggregate_edges(edge_specs, member_map, preserve_parallel)
    scopes = _presentation_scopes(scope_tree, relationships)
    current = dict(scopes[scope_id])
    overflow = _overflow_document(overflow_plan, len(nodes))
    return ProjectionResult(
        current,
        _breadcrumbs(scope_id, scopes),
        nodes,
        edges,
        overflow_plan.total_count,
        overflow,
        buckets,
    )


def search_canonical(
    scope_tree: ScopeTreeResult,
    query: str,
    relationships: RelationshipResult | None = None,
    *,
    limit: int = DIRECT_ITEM_LIMIT,
) -> tuple[dict[str, Any], ...]:
    """Search the complete canonical graph, independent of projection limits."""
    needle = str(query).strip().casefold()
    if not needle:
        return ()
    relations = relationships or RelationshipResult((), ())
    matches: list[dict[str, Any]] = []
    for scope in scope_tree.scopes:
        if needle in _search_text(scope):
            matches.append(_scope_match(scope, scope_tree, relations, limit))
    for entity in scope_tree.entities:
        if needle in _search_text(entity):
            matches.append(_entity_match(entity, scope_tree, relations, limit))
    return tuple(sorted(matches, key=lambda item: item["canonical_id"]))


class _ProjectionState:
    def __init__(self, scope_tree, relationships, scope_id, layers, include_configured_segments=False):
        self.scope_tree = scope_tree
        self.relationships = relationships
        self.scopes = {item["id"]: item for item in scope_tree.scopes}
        self.entities = {item["id"]: item for item in scope_tree.entities}
        self.parents = {
            item["id"]: item["parent_scope_id"] for item in scope_tree.scopes
        }
        if scope_id not in self.scopes:
            raise ValueError(f"unknown topology scope {scope_id}")
        self.scope_id = scope_id
        self.layers = frozenset(str(item) for item in layers) if layers else None
        self.include_configured_segments = include_configured_segments
        self.visible_relations = tuple(
            item for item in relationships.relations
            if self.layers is None or item.get("layer") in self.layers
        )
        if self.layers is not None and not include_configured_segments:
            # Interface subnets whose only edges are excluded layers are not
            # logical neighbours. Standalone subnet facts remain available;
            # the complete canonical graph is preserved.
            incident = {endpoint["ref_id"] for relation in relationships.relations
                        for endpoint in relation.get("endpoints", ())}
            visible = {endpoint["ref_id"] for relation in self.visible_relations
                       for endpoint in relation.get("endpoints", ())}
            self.entities = {
                key: item for key, item in self.entities.items()
                if not (item.get("kind") == "network_segment"
                        and key in incident and key not in visible)
            }
        self.nodes: dict[str, dict[str, Any]] = {}

    def project_items_and_relations(self):
        self._add_direct_nodes()
        edge_specs = []
        for relation in sorted(
            self.visible_relations, key=lambda item: item["id"]
        ):
            endpoints = self._mapped_relation_endpoints(relation)
            if endpoints is not None and endpoints[0] != endpoints[1]:
                edge_specs.append((relation, endpoints))
        items = tuple(sorted(self.nodes.values(), key=lambda item: item["id"]))
        return items, tuple(edge_specs)

    def _add_direct_nodes(self):
        for scope in self.scopes.values():
            if (scope["parent_scope_id"] == self.scope_id
                    and (self.layers is None or self._scope_has_content(scope["id"])
                         or self.include_configured_segments and scope.get("attributes", {}).get("network_context_type") in {"vlan", "vrf"})):
                node = _scope_ref(scope)
                self.nodes[node["id"]] = node
        for entity in self.entities.values():
            if entity["scope_id"] == self.scope_id:
                node = dict(entity)
                node["projection_kind"] = "entity"
                self.nodes[node["id"]] = node
            elif self.scope_id in entity.get("scope_memberships", ()):
                node = _entity_ref(entity, self.scope_id, "membership")
                self.nodes[node["id"]] = node

    def _scope_has_content(self, scope_id):
        return any(
            _is_descendant(item["scope_id"], scope_id, self.parents)
            or any(_is_descendant(member, scope_id, self.parents)
                   for member in item.get("scope_memberships", ()))
            for item in self.entities.values()
        )

    def _mapped_relation_endpoints(self, relation):
        canonical_ids = tuple(
            str(item["ref_id"]) for item in relation.get("endpoints", ())
        )
        if len(canonical_ids) != 2 or any(
            item not in self.entities for item in canonical_ids
        ):
            return None
        entities = tuple(self.entities[item] for item in canonical_ids)
        if relation.get("relation_type") == "configured_route":
            boundaries = [item for item in entities if item.get("role") == "configured_next_hop"]
            if boundaries and not any(self._touches_current_scope(item) for item in boundaries):
                return None
        if not any(self._touches_current_scope(item) for item in entities):
            return None
        return tuple(self._map_endpoint(item) for item in entities)

    def _touches_current_scope(self, entity):
        return _is_descendant(
            entity["scope_id"], self.scope_id, self.parents
        ) or self.scope_id in entity.get("scope_memberships", ())

    def _map_endpoint(self, entity):
        if entity["scope_id"] == self.scope_id:
            return entity["id"]
        if self.scope_id in entity.get("scope_memberships", ()):
            return f"entity-ref:{self.scope_id}:{entity['id']}"
        child = _direct_child(entity["scope_id"], self.scope_id, self.parents)
        if child is not None:
            return f"scope-ref:{child}"
        node = _entity_ref(entity, self.scope_id, "external")
        self.nodes[node["id"]] = node
        return node["id"]


def _scope_ref(scope):
    output = {
        "id": f"scope-ref:{scope['id']}",
        "projection_kind": "scope_ref",
        "kind": "scope",
        "importance": "normal",
        "ref_id": scope["id"],
        "name": scope["name"],
        "scope_type": scope["scope_type"],
        "summary": dict(scope.get("summary", {})),
    }
    if scope.get("attributes"):
        output["attributes"] = dict(scope["attributes"])
    return output


def _entity_ref(entity, scope_id, reason):
    return {
        "id": f"entity-ref:{scope_id}:{entity['id']}",
        "projection_kind": "entity_ref",
        "kind": entity.get("kind", "entity"),
        "importance": entity.get("importance", "normal"),
        "ref_id": entity["id"],
        "owner_scope_id": entity["scope_id"],
        "name": entity.get("name", entity["id"]),
        "role": entity.get("role"),
        "attributes": dict(entity.get("attributes", {})),
        "ref_reason": reason,
    }


def _direct_child(candidate_scope, current_scope, parents):
    if not _is_descendant(candidate_scope, current_scope, parents):
        return None
    current = candidate_scope
    while parents[current] != current_scope:
        current = parents[current]
    return current


def _is_descendant(candidate_scope, ancestor_scope, parents):
    current = candidate_scope
    while current is not None:
        if current == ancestor_scope:
            return True
        current = parents.get(current)
    return False


def _visible_nodes(plan):
    if not plan.overflowed:
        mapping = {item["id"]: item["id"] for item in plan.visible_items}
        return plan.visible_items, (), mapping
    buckets = tuple(item.as_node() for item in plan.buckets)
    mapping = {
        member_id: bucket.id
        for bucket in plan.buckets
        for member_id in bucket.member_ids
    }
    return buckets, buckets, mapping


def _aggregate_edges(edge_specs, visible_map, preserve_parallel=False):
    grouped: dict[tuple[str, str, str, str, str, str], list[Mapping[str, Any]]] = defaultdict(
        list
    )
    for relation, endpoints in edge_specs:
        visible = tuple(visible_map[item] for item in endpoints)
        if visible[0] == visible[1]:
            continue
        direction = str(relation.get("direction") or "bidirectional")
        physical = preserve_parallel and relation.get("layer") == "physical"
        left, right = visible if direction == "unidirectional" or physical else sorted(visible)
        detail_key = (str(relation["id"]) if physical else
                      str(relation.get("attributes", {}).get("protocol") or "")
                      if relation.get("relation_type") == "routing_adjacency" else "")
        key = (
            left,
            right,
            str(relation.get("layer", "")),
            str(relation["relation_type"]),
            direction,
            detail_key,
        )
        if visible[0] != left and relation.get("attributes"):
            relation = dict(relation)
            attributes = dict(relation["attributes"])
            for local, remote in (("local_asn", "remote_asn"), ("local_port", "remote_port")):
                left_value, right_value = attributes.pop(local, None), attributes.pop(remote, None)
                if right_value is not None:
                    attributes[local] = right_value
                if left_value is not None:
                    attributes[remote] = left_value
            relation["attributes"] = attributes
        grouped[key].append(relation)
    edges = [_aggregate_edge(key, grouped[key]) for key in sorted(grouped)]
    return tuple(sorted(edges, key=lambda item: item["id"]))


def _aggregate_edge(key, relations):
    left, right, layer, relation_type, direction, _detail = key
    member_ids = sorted(
        {
            member
            for relation in relations
            for member in (relation["id"], *relation.get("member_relation_ids", ()))
        }
    )
    identifier = _aggregate_edge_id(left, right, layer, relation_type, member_ids)
    return {
        "id": identifier,
        "multi_edge_identity": {
            "endpoint_key": f"{left}|{right}",
            "layer": layer,
            "relation_type": relation_type,
            "member_relation_ids": member_ids,
        },
        "relation_type": relation_type,
        "layer": layer,
        "direction": direction,
        "endpoints": [
            {"ref_type": "projection", "ref_id": left},
            {"ref_type": "projection", "ref_id": right},
        ],
        "count": len(relations),
        "summary": _relation_summary(relations),
        "member_relation_ids": member_ids,
        "attributes": _presentation_edge_attributes(relations),
    }


def _presentation_edge_attributes(relations):
    output = {}
    for key in ("local_port", "remote_port", "media_type", "speed_bps", "protocol",
                "local_asn", "remote_asn", "area", "tunnel_type", "protected_prefixes", "vlan_id"):
        values = [item.get("attributes", {})[key] for item in relations if key in item.get("attributes", {})]
        if values and all(value == values[0] for value in values):
            output[key] = values[0]
    for key in ("route_prefixes", "interface_names", "vlan_ids"):
        values = sorted({value for item in relations
                         for value in item.get("attributes", {}).get(key, ())})
        if values:
            output[key] = values
    next_hops = sorted({item["attributes"]["next_hop_ip"] for item in relations
                       if item.get("attributes", {}).get("next_hop_ip")})
    if len(next_hops) == 1:
        output["next_hop_ip"] = next_hops[0]
    if any(item["relation_type"] == "configured_route" for item in relations):
        output["route_count"] = sum(item.get("attributes", {}).get("route_count", 1) for item in relations)
    return output


def _aggregate_edge_id(left, right, layer, relation_type, member_ids):
    identity = [left, right, layer, relation_type, *member_ids]
    raw = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
    return f"aggregate-edge:{sha256(raw.encode()).hexdigest()}"


def _relation_summary(relations):
    configured = Counter(
        str(item.get("configured_state", "unknown")) for item in relations
    )
    operational = Counter(
        str(item.get("operational_state", "unknown")) for item in relations
    )
    return {
        "configured": dict(sorted(configured.items())),
        "operational": dict(sorted(operational.items())),
    }


def _overflow_document(plan, visible_count):
    if not plan.overflowed:
        return None
    return {
        "total_count": plan.total_count,
        "visible_count": visible_count,
        "strategy": plan.strategy,
        "bucket_count": len(plan.buckets),
    }


def _presentation_scopes(scope_tree, relationships):
    scopes = {item["id"]: dict(item) for item in scope_tree.scopes}
    entities = {item["id"]: item for item in scope_tree.entities}
    counts = Counter()
    for relation in relationships.relations:
        endpoint_scopes = _relation_scope_ids(relation, entities)
        if len(endpoint_scopes) > 1 and relation.get("owner_scope_id") in scopes:
            counts[str(relation["owner_scope_id"])] += 1
    for scope_id, scope in scopes.items():
        summary = dict(scope.get("summary", {}))
        summary["cross_scope_relation_count"] = counts[scope_id]
        scope["summary"] = summary
    return scopes


def _relation_scope_ids(relation, entities):
    return {
        entities[item["ref_id"]]["scope_id"]
        for item in relation.get("endpoints", ())
        if item.get("ref_id") in entities
    }


def _breadcrumbs(scope_id, scopes):
    values = []
    current = scope_id
    while current is not None:
        values.append(dict(scopes[current]))
        current = scopes[current]["parent_scope_id"]
    return tuple(reversed(values))


def _scope_match(scope, scope_tree, relationships, limit):
    parent = scope.get("parent_scope_id")
    bucket_id = None
    if parent is not None:
        items = _location_items(scope_tree, relationships, parent)
        bucket_id = locate_item_bucket(
            parent, items, f"scope-ref:{scope['id']}", limit=limit
        )
    return {
        "canonical_id": scope["id"],
        "kind": "scope",
        "name": scope.get("name", scope["id"]),
        "scope_id": scope["id"],
        "parent_scope_id": parent,
        "bucket_id": bucket_id,
    }


def _entity_match(entity, scope_tree, relationships, limit):
    scope_id = str(entity["scope_id"])
    items = _location_items(scope_tree, relationships, scope_id)
    bucket_id = locate_item_bucket(scope_id, items, str(entity["id"]), limit=limit)
    return {
        "canonical_id": entity["id"],
        "kind": entity.get("kind", "entity"),
        "name": entity.get("name", entity["id"]),
        "scope_id": scope_id,
        "bucket_id": bucket_id,
    }


def _location_items(scope_tree, relationships, scope_id):
    state = _ProjectionState(scope_tree, relationships, scope_id, None)
    items, _ = state.project_items_and_relations()
    return items


def _search_text(item):
    values = [
        item.get("id"),
        item.get("name"),
        item.get("role"),
        item.get("scope_type"),
        *item.get("attributes", {}).get("network_prefixes", ()),
    ]
    return "\n".join(str(value).casefold() for value in values if value is not None)
