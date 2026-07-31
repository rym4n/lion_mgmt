"""Canonical topology relations and evidence-preserving device collapse."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from ipaddress import ip_address, ip_interface, ip_network
import json
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from services.network_topology.identity import IdentityResolution
from services.network_topology.scope_tree import (
    ScopeTreeResult,
    lowest_common_ancestor,
    network_segment_id,
)


RELATION_SOURCES = (
    "adjacencies",
    "routing_relationships",
    "tunnels",
    "gateways",
    "security_boundaries",
    "wan_connections",
)


@dataclass(frozen=True, slots=True)
class RelationshipIssue:
    reason_code: str
    subject_ids: tuple[str, ...]
    details: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RelationshipResult:
    relations: tuple[dict[str, Any], ...]
    unresolved: tuple[RelationshipIssue, ...]


@dataclass(frozen=True, slots=True)
class _CollapseEdge:
    fact: Mapping[str, Any]
    left: str
    right: str
    interface_id: str
    compatibility: tuple[str, str, tuple[str, ...]]


def build_relationships(
    facts: Mapping[str, Any],
    resolution: IdentityResolution,
    scope_tree: ScopeTreeResult,
    *,
    profile_revision: int = 1,
) -> RelationshipResult:
    """Build globally stored relations without creating projection-only objects."""
    state = _RelationshipState(facts, resolution, scope_tree, profile_revision)
    relations = state.build_regular_relations()
    relations.extend(state.build_collapsed_paths())
    merged = _merge_duplicate_relations(relations)
    return RelationshipResult(
        tuple(sorted(merged, key=lambda item: item["id"])),
        tuple(sorted(state.issues, key=_issue_key)),
    )


class _RelationshipState:
    def __init__(self, facts, resolution, scope_tree, profile_revision):
        self.facts = facts
        self.resolution = resolution
        self.scope_tree = scope_tree
        self.profile_revision = profile_revision
        self.issues: list[RelationshipIssue] = []
        self.fact_index = _fact_index(facts)
        self.interfaces = {item["id"]: item for item in facts.get("interfaces", ())}
        self.entities = {item["id"]: item for item in scope_tree.entities}
        self.parents = {
            item["id"]: item["parent_scope_id"] for item in scope_tree.scopes
        }
        self.collapsed = set(scope_tree.collapsed_fact_ids)
        self.member_groups = _member_group_index(resolution)
        self.segments = _segment_endpoint_index(facts, self.entities)
        self.provenance = {
            item["id"]: item["provenance"]
            for item in facts.get("provenance_catalog", ())
        }

    def build_regular_relations(self):
        relations: list[dict[str, Any]] = []
        for source in RELATION_SOURCES:
            for fact in sorted(self.facts.get(source, ()), key=lambda item: item["id"]):
                if source == "adjacencies" and self._touches_collapsed(fact):
                    continue
                relation = self._relation(source, fact)
                if relation is not None:
                    relations.append(relation)
        return relations

    def build_collapsed_paths(self):
        edges = [
            edge
            for fact in sorted(self.facts.get("adjacencies", ()), key=lambda x: x["id"])
            if (edge := self._collapse_edge(fact)) is not None
        ]
        components = _collapse_components(edges, self.collapsed)
        output: list[dict[str, Any]] = []
        for collapsed_ids, component_edges in components:
            relation = self._collapse_component(collapsed_ids, component_edges)
            if relation is not None:
                output.append(relation)
        return output

    def _relation(self, source, fact):
        builder = {
            "adjacencies": self._adjacency_relation,
            "routing_relationships": self._routing_relation,
            "tunnels": self._tunnel_relation,
            "gateways": self._gateway_relation,
            "security_boundaries": self._security_relation,
            "wan_connections": self._wan_relation,
        }[source]
        specification = builder(fact)
        if specification is None:
            self._unresolved("RELATION_ENDPOINT_UNRESOLVED", (fact["id"],))
            return None
        endpoints, relation_type, layer, attachment, context, attributes = specification
        return self._document(
            fact, endpoints, relation_type, layer, attachment, context, attributes
        )

    def _adjacency_relation(self, fact):
        attributes = fact["attributes"]
        endpoints = self._endpoints(attributes["local_id"], attributes["remote_id"])
        if endpoints is None:
            return None
        layer = str(attributes["layer"])
        relation_type = {
            "physical": "physical_link",
            "l2": "l2_trunk",
            "l3": "l3_connectivity",
        }[layer]
        interface = self.interfaces.get(attributes["local_id"], {})
        interface_attributes = interface.get("attributes", {})
        attachment = _joined(
            (
                *attributes.get("attachments", ()),
                attributes.get("link_group_id"),
                interface_attributes.get("lag_id"),
            )
        )
        context = self._context(attributes.get("context_id"), attributes["local_id"])
        return endpoints, relation_type, layer, attachment, context, {}

    def _routing_relation(self, fact):
        attributes = fact["attributes"]
        endpoints = self._endpoints(attributes["local_id"], attributes["remote_id"])
        if endpoints is None:
            return None
        attachment = _joined(
            (attributes.get("interface_id"), attributes.get("protocol"))
        )
        kept = _selected(attributes, "protocol", "local_asn", "remote_asn", "area")
        return (
            endpoints,
            "routing_adjacency",
            "routing",
            attachment,
            str(attributes.get("vrf_id") or ""),
            kept,
        )

    def _tunnel_relation(self, fact):
        attributes = fact["attributes"]
        endpoints = self._endpoints(
            attributes["local_endpoint"], attributes["remote_endpoint"]
        )
        if endpoints is None:
            return None
        attachment = _joined(
            (attributes.get("tunnel_id"), attributes.get("interface_id"))
        )
        kept = _selected(attributes, "tunnel_type", "protected_prefixes")
        return (
            endpoints,
            "tunnel",
            "overlay",
            attachment,
            str(attributes.get("context_id") or ""),
            kept,
        )

    def _gateway_relation(self, fact):
        attributes = fact["attributes"]
        endpoints = self._endpoints(attributes["gateway_id"], attributes["segment_id"])
        if endpoints is None:
            return None
        segment = self.fact_index.get(attributes["segment_id"], {})
        segment_attributes = segment.get("attributes", {})
        context = segment_attributes.get("vrf_id") or segment_attributes.get(
            "context_id"
        )
        kept = _selected(attributes, "virtual_address")
        return endpoints, "gateway_for", "service", "", str(context or ""), kept

    def _security_relation(self, fact):
        attributes = fact["attributes"]
        segment_id = self._interface_segment(attributes["interface_id"])
        endpoints = self._endpoints(attributes["device_id"], segment_id or "")
        if endpoints is None:
            return None
        interface = self.interfaces.get(attributes["interface_id"], {})
        interface_attributes = interface.get("attributes", {})
        context = interface_attributes.get("vrf_id") or interface_attributes.get(
            "context_id"
        )
        kept = {"zone": _zone_name(attributes["zone_id"], self.fact_index)}
        return (
            endpoints,
            "security_boundary",
            "security",
            str(attributes["interface_id"]),
            str(context or ""),
            kept,
        )

    def _wan_relation(self, fact):
        attributes = fact["attributes"]
        endpoints = self._endpoints(attributes["local_id"], attributes["remote_id"])
        if endpoints is None:
            return None
        attachment = str(
            attributes.get("circuit_id") or attributes.get("connection_type") or ""
        )
        context = self._wan_context(attributes["remote_id"])
        kept = _selected(attributes, "provider")
        return endpoints, "wan_connection", "l3", attachment, context, kept

    def _document(
        self, fact, endpoints, relation_type, layer, attachment, context, attributes
    ):
        endpoint_key = "|".join(sorted(endpoints))
        identity = {
            "endpoint_key": endpoint_key,
            "layer": layer,
            "relation_type": relation_type,
            "attachment_key": attachment,
            "context_key": context,
        }
        endpoint_documents = [
            {"ref_type": "entity", "ref_id": endpoint} for endpoint in endpoints
        ]
        return {
            "id": _relation_id(fact["id"]),
            "multi_edge_identity": identity,
            "relation_type": relation_type,
            "layer": layer,
            "direction": "bidirectional",
            "endpoints": endpoint_documents,
            "owner_scope_id": self._owner_scope(endpoints),
            "attributes": attributes,
            "member_fact_ids": [fact["id"]],
            "member_relation_ids": [],
            "configured_state": _configured_state(fact),
            "operational_state": _operational_state(fact, relation_type),
            "provenance": self._fact_provenance(fact),
        }

    def _endpoints(self, left, right):
        resolved = self._resolve_endpoint(left), self._resolve_endpoint(right)
        if None in resolved or resolved[0] == resolved[1]:
            return None
        return resolved

    def _resolve_endpoint(self, reference):
        reference = str(reference)
        if reference in self.interfaces:
            reference = str(self.interfaces[reference]["attributes"]["device_id"])
        if reference in self.segments:
            return self.segments[reference]
        canonical = self.resolution.fact_identity_map.get(reference, reference)
        canonical = self.member_groups.get(canonical, canonical)
        if canonical in self.entities:
            return canonical
        return self._address_endpoint(reference)

    def _address_endpoint(self, value):
        try:
            address = ip_address(value)
        except ValueError:
            return None
        owners = {
            self._resolve_endpoint(item["id"])
            for item in self.interfaces.values()
            if _interface_has_address(item, address)
        }
        owners.discard(None)
        return next(iter(owners)) if len(owners) == 1 else None

    def _owner_scope(self, endpoints):
        scope_ids = [self.entities[item]["scope_id"] for item in endpoints]
        return lowest_common_ancestor(scope_ids, self.parents)

    def _context(self, context_id, interface_id):
        interface = self.interfaces.get(interface_id, {})
        attributes = interface.get("attributes", {})
        parts = [str(context_id)] if context_id else []
        interface_context = attributes.get("vrf_id") or attributes.get("context_id")
        if interface_context and str(interface_context) not in parts:
            parts.append(str(interface_context))
        parts.extend(f"vlan={item}" for item in sorted(attributes.get("vlan_ids", ())))
        return "|".join(parts)

    def _wan_context(self, remote_id):
        fact = self.fact_index.get(remote_id, {})
        attributes = fact.get("attributes", {})
        endpoint_id = self.segments.get(str(remote_id))
        endpoint = self.entities.get(endpoint_id or "", {})
        if endpoint.get("kind") == "external" and not (
            attributes.get("vrf_id") or attributes.get("context_id")
        ):
            return "public"
        return str(
            attributes.get("vrf_id")
            or attributes.get("context_id")
            or attributes.get("network_domain_id")
            or ""
        )

    def _interface_segment(self, interface_id):
        interface = self.interfaces.get(interface_id, {})
        addresses = interface.get("attributes", {}).get("addresses", ())
        matches = []
        for segment_id, endpoint_id in self.segments.items():
            segment = self.fact_index.get(segment_id, {})
            cidr = segment.get("attributes", {}).get("canonical_cidr")
            if cidr and any(_address_in_network(value, cidr) for value in addresses):
                matches.append(endpoint_id)
        return next(iter(set(matches))) if len(set(matches)) == 1 else None

    def _touches_collapsed(self, fact):
        attributes = fact["attributes"]
        local = self.interfaces.get(attributes["local_id"], {})
        local_id = local.get("attributes", {}).get("device_id", attributes["local_id"])
        return local_id in self.collapsed or attributes["remote_id"] in self.collapsed

    def _collapse_edge(self, fact):
        if not self._touches_collapsed(fact):
            return None
        attributes = fact["attributes"]
        interface_id = str(attributes["local_id"])
        interface = self.interfaces.get(interface_id)
        if interface is None:
            self._unresolved("COLLAPSE_PATH_AMBIGUOUS", (fact["id"],))
            return None
        local = str(interface["attributes"]["device_id"])
        remote = str(attributes["remote_id"])
        left = local if local in self.collapsed else self._resolve_endpoint(local)
        right = remote if remote in self.collapsed else self._resolve_endpoint(remote)
        if left is None or right is None or left == right:
            self._unresolved("COLLAPSE_PATH_AMBIGUOUS", (fact["id"],))
            return None
        context = self._context(attributes.get("context_id"), interface_id)
        compatibility = str(attributes["layer"]), context, ()
        return _CollapseEdge(fact, left, right, interface_id, compatibility)

    def _collapse_component(self, collapsed_ids, edges):
        graph = _edge_graph(edges)
        boundaries = sorted(set(graph).difference(collapsed_ids))
        degrees = {item: len(graph[item]) for item in collapsed_ids}
        subject_ids = tuple(sorted(collapsed_ids))
        if not boundaries and degrees and all(value == 2 for value in degrees.values()):
            self._unresolved("COLLAPSE_PATH_CYCLE", subject_ids)
            return None
        if len(boundaries) != 2 or any(value != 2 for value in degrees.values()):
            self._unresolved("COLLAPSE_PATH_BRANCH", subject_ids)
            return None
        if len(edges) != len(graph) - 1:
            self._unresolved("COLLAPSE_PATH_AMBIGUOUS", subject_ids)
            return None
        if len({edge.compatibility for edge in edges}) != 1:
            self._unresolved("COLLAPSE_PATH_INCOMPATIBLE", subject_ids)
            return None
        ordered_edges = _walk_unique_path(boundaries[0], boundaries[1], graph)
        if ordered_edges is None:
            self._unresolved("COLLAPSE_PATH_AMBIGUOUS", subject_ids)
            return None
        return self._collapsed_document(boundaries, ordered_edges, collapsed_ids)

    def _collapsed_document(self, boundaries, ordered_edges, collapsed_ids):
        layer, context, vlans = ordered_edges[0].compatibility
        context_key = _joined((context, *(f"vlan={item}" for item in vlans)))
        member_ids = _ordered_collapse_members(
            ordered_edges, collapsed_ids, self.interfaces
        )
        relation_type = self._collapse_relation_type(boundaries)
        identity = {
            "endpoint_key": "|".join(boundaries),
            "layer": layer,
            "relation_type": relation_type,
            "attachment_key": relation_type,
            "context_key": context_key,
        }
        identifier = "relation:collapsed-" + _safe("|".join(sorted(collapsed_ids)))
        return {
            "id": identifier,
            "multi_edge_identity": identity,
            "relation_type": relation_type,
            "layer": layer,
            "direction": "bidirectional",
            "endpoints": [
                {"ref_type": "entity", "ref_id": item} for item in boundaries
            ],
            "owner_scope_id": self._owner_scope(boundaries),
            "attributes": {
                "collapsed_role": _collapsed_role(collapsed_ids, self.fact_index),
                "ordered_member_fact_ids": member_ids,
            },
            "member_fact_ids": member_ids,
            "member_relation_ids": [],
            "configured_state": "present",
            "operational_state": _combined_state(ordered_edges),
            "provenance": [
                {
                    "type": "derived",
                    "rule_id": "profile.collapse-access/v1",
                    "profile_revision": self.profile_revision,
                    "member_ids": member_ids,
                }
            ],
        }

    def _collapse_relation_type(self, boundaries):
        endpoint_scopes = {self.entities[item]["scope_id"] for item in boundaries}
        if len(endpoint_scopes) > 1:
            return "aggregated_connectivity"
        return "collapsed_path"

    def _fact_provenance(self, fact):
        values = [
            self.provenance[item]
            for item in fact.get("provenance_ids", ())
            if item in self.provenance
        ]
        return values or [
            {
                "type": "derived",
                "rule_id": "relation.from-fact/v1",
                "profile_revision": self.profile_revision,
                "member_ids": [fact["id"]],
            }
        ]

    def _unresolved(self, reason_code, subject_ids, **details):
        self.issues.append(RelationshipIssue(reason_code, tuple(subject_ids), details))


def _fact_index(facts):
    index = {}
    for value in facts.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, Mapping) and "id" in item:
                index[item["id"]] = item
    return index


def _member_group_index(resolution):
    return {
        member: group.canonical_id
        for group in resolution.groups
        for member in group.physical_member_ids
    }


def _segment_endpoint_index(facts, entities):
    output = {}
    for fact in facts.get("segments", ()):
        attributes = fact.get("attributes", {})
        computed = network_segment_id(
            attributes.get("network_domain_id") or "unassigned",
            attributes.get("vrf_id") or attributes.get("context_id") or "default",
            attributes["canonical_cidr"],
        )
        if fact["id"] in entities:
            output[fact["id"]] = fact["id"]
        elif computed in entities:
            output[fact["id"]] = computed
    return output


def _collapse_components(edges, collapsed):
    incident = defaultdict(list)
    for edge in edges:
        incident[edge.left].append(edge)
        incident[edge.right].append(edge)
    pending = set(collapsed).intersection(incident)
    components = []
    while pending:
        start = min(pending)
        collapsed_ids, component_edges = _collect_component(start, incident, collapsed)
        pending.difference_update(collapsed_ids)
        components.append((collapsed_ids, component_edges))
    return components


def _collect_component(start, incident, collapsed):
    pending = [start]
    visited = set()
    edges = {}
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        for edge in incident[current]:
            edges[edge.fact["id"]] = edge
            other = edge.right if edge.left == current else edge.left
            if other in collapsed and other not in visited:
                pending.append(other)
    ordered = tuple(edges[key] for key in sorted(edges))
    return frozenset(visited), ordered


def _edge_graph(edges):
    graph = defaultdict(list)
    for edge in edges:
        graph[edge.left].append(edge)
        graph[edge.right].append(edge)
    for value in graph.values():
        value.sort(key=lambda edge: edge.fact["id"])
    return graph


def _walk_unique_path(start, target, graph):
    ordered = []
    previous = None
    current = start
    seen = {start}
    while current != target:
        candidates = [edge for edge in graph[current] if edge is not previous]
        if len(candidates) != 1:
            return None
        edge = candidates[0]
        next_node = edge.right if edge.left == current else edge.left
        if next_node in seen:
            return None
        ordered.append(edge)
        seen.add(next_node)
        previous, current = edge, next_node
    return tuple(ordered)


def _ordered_collapse_members(edges, collapsed, interfaces):
    output = []
    included_devices = set()
    for edge in edges:
        for endpoint in (edge.left, edge.right):
            if endpoint in collapsed and endpoint not in included_devices:
                output.append(endpoint)
                included_devices.add(endpoint)
        interface = interfaces.get(edge.interface_id, {})
        owner = interface.get("attributes", {}).get("device_id")
        if owner in collapsed:
            output.append(edge.interface_id)
        output.append(edge.fact["id"])
    return output


def _merge_duplicate_relations(relations):
    grouped = defaultdict(list)
    for relation in relations:
        identity = relation["multi_edge_identity"]
        key = tuple(
            identity[item]
            for item in (
                "endpoint_key",
                "layer",
                "relation_type",
                "attachment_key",
                "context_key",
            )
        )
        grouped[key].append(relation)
    return [_merge_group(grouped[key]) for key in sorted(grouped)]


def _merge_group(relations):
    ordered = sorted(relations, key=lambda item: item["id"])
    if len(ordered) == 1:
        return ordered[0]
    output = dict(ordered[0])
    output["member_fact_ids"] = sorted(
        {item for relation in ordered for item in relation["member_fact_ids"]}
    )
    output["member_relation_ids"] = sorted(
        {item for relation in ordered for item in relation["member_relation_ids"]}
    )
    output["provenance"] = _unique_mappings(
        item for relation in ordered for item in relation["provenance"]
    )
    return output


def _unique_mappings(values: Iterable[Mapping[str, Any]]):
    by_key = {
        json.dumps(value, sort_keys=True, separators=(",", ":")): value
        for value in values
    }
    return [by_key[key] for key in sorted(by_key)]


def _configured_state(fact):
    value = str(fact.get("attributes", {}).get("configured_state") or "present")
    return value if value in {"present", "absent", "unknown"} else "unknown"


def _operational_state(fact, relation_type):
    attributes = fact.get("attributes", {})
    value = attributes.get("operational_state")
    value = value or attributes.get("session_state") or attributes.get("state")
    if value is None and relation_type == "gateway_for":
        value = "up"
    normalized = str(value or "unknown").lower()
    return (
        normalized if normalized in {"up", "down", "degraded", "unknown"} else "unknown"
    )


def _combined_state(edges):
    states = {_operational_state(edge.fact, "collapsed_path") for edge in edges}
    if states == {"up"}:
        return "up"
    if "down" in states:
        return "down"
    if "degraded" in states:
        return "degraded"
    return "unknown"


def _collapsed_role(collapsed_ids, facts):
    roles = []
    for identifier in sorted(collapsed_ids):
        fact = facts.get(identifier, {})
        roles.extend(str(item) for item in fact.get("attributes", {}).get("roles", ()))
    return min(roles) if roles else "access_switch"


def _relation_id(fact_id):
    return "relation:" + str(fact_id).split(":", 1)[-1]


def _selected(attributes, *keys):
    return {
        key: attributes[key]
        for key in keys
        if key in attributes and attributes[key] not in (None, "", [], ())
    }


def _joined(values):
    return "|".join(sorted({str(item) for item in values if item not in (None, "")}))


def _zone_name(zone_id, facts):
    attributes = facts.get(zone_id, {}).get("attributes", {})
    return str(attributes.get("name") or attributes.get("local_id") or zone_id)


def _interface_has_address(interface, address):
    values = interface.get("attributes", {}).get("addresses", ())
    return any(ip_interface(str(value)).ip == address for value in values)


def _address_in_network(address, cidr):
    return ip_interface(str(address)).ip in ip_network(str(cidr), strict=False)


def _issue_key(issue):
    return issue.reason_code, issue.subject_ids


def _safe(value):
    return quote(str(value), safe="-._~:")
