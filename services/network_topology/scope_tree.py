"""Scope-tree and canonical-entity construction for formal topology Facts."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_network
import re
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from services.network_topology.identity import IdentityResolution
from services.network_topology.context_placement import context_memberships, context_type
from services.network_topology.address_presentation import network_attributes
from services.network_topology.configured_routes import configured_routes, route_attributes
from services.network_topology.device_presentation import interface_presentations
from services.network_topology.importance import (
    ImportanceDecision,
    assess_importance,
    logical_group_importance,
    normal_importance,
)


ROOT_SCOPE_ID = "scope:organization:root"
UNASSIGNED_SCOPE_ID = "scope:custom:unassigned"
SCOPE_TYPES = {
    "organization": "organization",
    "region": "region",
    "site": "site",
    "network_domain": "network_domain",
    "security_zone": "security_zone",
    "vrf": "network_context",
    "network_context": "network_context",
}


class ScopeTreeError(ValueError):
    """Raised when authoritative scope input cannot form one valid tree."""


@dataclass(frozen=True, slots=True)
class ScopeProjectionIssue:
    reason_code: str
    subject_id: str
    details: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ScopeTreeResult:
    root_scope_id: str
    scopes: tuple[dict[str, Any], ...]
    entities: tuple[dict[str, Any], ...]
    unresolved: tuple[ScopeProjectionIssue, ...]
    excluded_fact_ids: tuple[str, ...]
    collapsed_fact_ids: tuple[str, ...]

    def as_graph(self) -> dict[str, Any]:
        return {
            "schema_version": "aioe.network-topology.graph/v2",
            "root_scope_id": self.root_scope_id,
            "scopes": [dict(item) for item in self.scopes],
            "entities": [dict(item) for item in self.entities],
            "relations": [],
        }


def build_scope_tree(
    facts: Mapping[str, Any],
    identity_resolution: IdentityResolution,
    profile: Mapping[str, Any],
    *,
    authoritative_scopes: Iterable[Mapping[str, Any]] = (),
    entity_memberships: Mapping[str, Iterable[str]] | None = None,
    profile_revision: int = 1,
) -> ScopeTreeResult:
    """Build Task 4.2 scopes and entities; relation ownership is a later phase."""
    _validate_inputs(facts, profile)
    explicit_scopes = tuple(authoritative_scopes)
    records = _scope_records(facts, explicit_scopes)
    index = _build_scope_index(records, profile, facts, profile_revision)
    memberships = entity_memberships or {}
    state = _ProjectionState(
        index, memberships, facts, profile, profile_revision,
        require_assignment=bool(explicit_scopes),
    )
    state.add_devices(identity_resolution)
    state.add_groups(identity_resolution)
    state.add_segments()
    state.add_configured_next_hops()
    scopes = _finalize_scopes(index, state.entities)
    return ScopeTreeResult(
        profile["root_scope_seed"], scopes, tuple(state.entities),
        tuple(state.issues), tuple(sorted(state.excluded)),
        tuple(sorted(state.collapsed)),
    )


class _ProjectionState:
    def __init__(
        self, scopes, memberships, facts, profile, profile_revision,
        *, require_assignment,
    ):
        self.scopes = scopes
        self.memberships = memberships
        self.facts = facts
        self.profile = profile
        self.profile_revision = profile_revision
        self.require_assignment = require_assignment
        self.entities: list[dict[str, Any]] = []
        self.issues: list[ScopeProjectionIssue] = []
        self.excluded: list[str] = []
        self.collapsed: list[str] = []
        self.provenance = _provenance_index(facts)
        self.context_memberships = context_memberships(facts)

    def add_devices(self, resolution):
        grouped = {member for group in resolution.groups for member in group.physical_member_ids}
        for fact in sorted(self.facts.get("devices", ()), key=lambda item: item["id"]):
            canonical_id = resolution.fact_identity_map.get(fact["id"])
            if canonical_id is None or canonical_id in grouped:
                continue
            if self._filter_device(fact, canonical_id):
                continue
            self.entities.append(self._device_entity(fact, canonical_id))

    def add_groups(self, resolution):
        facts = {item["id"]: item for item in self.facts.get("device_groups", ())}
        for group in sorted(resolution.groups, key=lambda item: item.canonical_id):
            local_id = _matching_group_fact(group, facts)
            fact = facts.get(local_id, _synthetic_group_fact(group))
            decision = logical_group_importance(fact)
            self.entities.append(self._group_entity(group, fact, decision))

    def add_segments(self):
        for fact in sorted(self.facts.get("segments", ()), key=lambda item: item["id"]):
            self.entities.append(self._segment_entity(fact))

    def add_configured_next_hops(self):
        interfaces = {item["id"]: item for item in self.facts.get("interfaces", ())}
        grouped = {}
        for (identifier, owner, address, interface_id), routes in configured_routes(self.facts):
            if owner not in {item["id"] for item in self.facts.get("devices", ())}:
                continue
            scope_id, memberships = (self._observed_context_scope(interface_id)
                                     if self.context_memberships.get(interface_id)
                                     else (self.profile["root_scope_seed"], []))
            if identifier in grouped:
                entity = grouped[identifier]
                entity["scope_memberships"] = sorted(set(entity["scope_memberships"] + memberships))
                parents = {key: value["parent_scope_id"] for key, value in self.scopes.items()}
                entity["scope_id"] = lowest_common_ancestor([entity["scope_id"], scope_id], parents)
                attrs = route_attributes(address, interfaces[interface_id], routes)
                for key in ("route_prefixes", "interface_names"):
                    entity["attributes"][key] = sorted(set(entity["attributes"][key] + attrs[key]))
                entity["attributes"]["route_count"] += len(routes)
                entity["provenance"].extend(entry for route in routes for entry in self._provenance(route))
                continue
            attrs = network_attributes(f"{address}/{'128' if ':' in address else '32'}")
            attrs.update(route_attributes(address, interfaces[interface_id], routes))
            provenance = [entry for route in routes for entry in self._provenance(route)]
            grouped[identifier] = _entity(identifier, scope_id, memberships, "external", address,
                "configured_next_hop", "external_network", normal_importance(routes[0]),
                attrs, provenance)
        self.entities.extend(grouped.values())

    def _filter_device(self, fact, canonical_id):
        attributes = fact.get("attributes", {})
        device_type = str(attributes.get("device_type", "device")).lower()
        if device_type in {"host", "terminal", "endpoint"}:
            if self._service_boundary_allowed(fact, device_type):
                self.entities.append(
                    self._service_boundary(fact, device_type, canonical_id)
                )
            else:
                self.excluded.append(fact["id"])
            return True
        roles = {str(item).lower() for item in attributes.get("roles", ())}
        decision = assess_importance(fact, self.facts, self.profile)
        if (_is_access_device(roles)
                and self.profile.get("access_device_policy") == "collapse"
                and decision.level == "normal"):
            self.collapsed.append(fact["id"])
            return True
        return False

    def _device_entity(self, fact, canonical_id):
        decision = assess_importance(fact, self.facts, self.profile)
        scope_id, memberships = self._owner_scope(fact["id"], canonical_id)
        attributes = _device_attributes(fact)
        interfaces = interface_presentations(self.facts, fact["id"])
        if interfaces:
            attributes["interfaces"] = interfaces
        roles = fact.get("attributes", {}).get("roles", ())
        return _entity(
            canonical_id, scope_id, memberships, "device",
            _entity_name(fact, canonical_id), str(roles[0] if roles else "network_device"),
            "network_device", decision, attributes, self._provenance(fact),
        )

    def _group_entity(self, group, fact, decision):
        scope_id, memberships = self._owner_scope(fact["id"], group.canonical_id)
        return _entity(
            group.canonical_id, scope_id, memberships, "device", group.identity,
            f"logical_{group.group_type}", "network_device", decision, {},
            self._provenance(fact),
        )

    def _segment_entity(self, fact):
        attributes = fact.get("attributes", {})
        cidr = str(attributes["canonical_cidr"])
        domain = str(attributes.get("network_domain_id") or "unassigned")
        vrf = str(attributes.get("vrf_id") or attributes.get("context_id") or "default")
        identifier = network_segment_id(domain, vrf, cidr)
        scope_id, memberships = self._segment_scope(fact, vrf)
        kind = "external" if attributes.get("device_id") is None else "network_segment"
        role = "external_network" if kind == "external" else "connected_network"
        output = network_attributes(cidr)
        output.update({key: attributes[key] for key in ("vlan_id", "vni", "context_id", "vrf_id")
                       if attributes.get(key) is not None})
        virtual_addresses = _segment_virtual_addresses(fact["id"], self.facts)
        if virtual_addresses:
            output["virtual_addresses"] = virtual_addresses
        return _entity(
            identifier, scope_id, memberships, kind,
            str(attributes.get("name") or output["network_prefixes"][0]), role,
            "external_network" if kind == "external" else "network_segment",
            normal_importance(fact), output, self._provenance(fact),
        )

    def _owner_scope(self, fact_id, canonical_id):
        raw = self.memberships.get(fact_id, self.memberships.get(canonical_id, ()))
        if not raw and self.context_memberships.get(fact_id):
            return self._observed_context_scope(fact_id)
        if not raw and self.require_assignment:
            return self._unassigned(fact_id, "MISSING_SCOPE_MEMBERSHIP", ())
        return self._resolve_memberships(fact_id, raw)

    def _segment_scope(self, fact, vrf):
        raw = fact.get("attributes", {}).get("scope_ids", ())
        if raw:
            return self._resolve_memberships(fact["id"], raw)
        if self.context_memberships.get(fact["id"]):
            return self._observed_context_scope(fact["id"])
        candidate = _scope_id("network_context", _context_code(vrf))
        if candidate in self.scopes:
            return candidate, []
        return self.profile["root_scope_seed"], []

    def _observed_context_scope(self, fact_id):
        memberships, _ = _normalize_memberships(self.context_memberships[fact_id], self.scopes)
        if not memberships:
            return self.profile["root_scope_seed"], []
        parents = {key: value["parent_scope_id"] for key, value in self.scopes.items()}
        return lowest_common_ancestor(memberships, parents), memberships

    def _resolve_memberships(self, subject_id, raw_memberships):
        normalized, unknown = _normalize_memberships(raw_memberships, self.scopes)
        if unknown:
            return self._unassigned(subject_id, "UNKNOWN_SCOPE_MEMBERSHIP", unknown)
        if _has_authoritative_conflict(normalized, self.scopes):
            return self._unassigned(subject_id, "AUTHORITATIVE_SCOPE_CONFLICT", normalized)
        if not normalized:
            return self.profile["root_scope_seed"], []
        parents = {key: value["parent_scope_id"] for key, value in self.scopes.items()}
        return lowest_common_ancestor(normalized, parents), normalized

    def _unassigned(self, subject_id, reason, values):
        _ensure_unassigned(self.scopes, self.facts, self.profile_revision)
        self.issues.append(ScopeProjectionIssue(reason, subject_id, {"memberships": tuple(values)}))
        return UNASSIGNED_SCOPE_ID, [UNASSIGNED_SCOPE_ID]

    def _service_boundary_allowed(self, fact, device_type):
        if not _has_authoritative_mark(fact):
            return False
        exceptions = self.profile.get("explicit_asset_exceptions", ())
        return any(item.get("asset_type") == device_type for item in exceptions)

    def _service_boundary(self, fact, device_type, canonical_id):
        exception = next(
            item for item in self.profile["explicit_asset_exceptions"]
            if item.get("asset_type") == device_type
        )
        attributes = {"boundary_type": device_type}
        source = fact.get("attributes", {})
        for key in exception.get("allowed_network_attributes", ()):
            if key in source:
                attributes[key] = source[key]
        scope_id, memberships = self._owner_scope(fact["id"], fact["id"])
        decision = ImportanceDecision("important", ())
        provenance = [_derived("profile.explicit_asset_exception/v1", self.profile_revision, [fact["id"]])]
        return _entity(
            canonical_id, scope_id, memberships,
            "service_boundary", _entity_name(fact, fact["id"]), "service_boundary",
            "service_boundary", decision, attributes, provenance,
            reasons=["authoritative_profile_exception"],
        )

    def _provenance(self, fact):
        values = [self.provenance[item] for item in fact.get("provenance_ids", ())
                  if item in self.provenance]
        return values or [_derived("profile.entity/v1", self.profile_revision, [fact["id"]])]


def lowest_common_ancestor(scope_ids, parents):
    """Return the deepest shared ancestor in a validated single-parent tree."""
    if not scope_ids:
        raise ScopeTreeError("LCA needs at least one scope")
    lineages = [_lineage(scope_id, parents) for scope_id in scope_ids]
    common = set(lineages[0]).intersection(*(set(item) for item in lineages[1:]))
    if not common:
        raise ScopeTreeError("scopes have no common ancestor")
    depth = {scope_id: len(_lineage(scope_id, parents)) for scope_id in common}
    return max(sorted(common), key=depth.__getitem__)


def network_segment_id(network_domain, vrf, cidr):
    """Include isolation context so overlapping address spaces never merge."""
    network = ip_network(str(cidr), strict=False)
    cidr_key = f"{network.network_address}-{network.prefixlen}"
    return f"network:{_safe(network_domain)}:{_safe(_context_code(vrf))}:{cidr_key}"


def _validate_inputs(facts, profile):
    if facts.get("schema_version") != "aioe.network-topology.facts/v1":
        raise ScopeTreeError("scope projection only accepts formal facts/v1")
    if profile.get("root_scope_seed") != ROOT_SCOPE_ID:
        raise ScopeTreeError("profile root_scope_seed is invalid")
    order = profile.get("grouping_order", ())
    if not order or len(order) != len(set(order)):
        raise ScopeTreeError("profile grouping_order must be unique")


def _scope_records(facts, authoritative_scopes):
    records = list(authoritative_scopes)
    used_contexts = {value for values in context_memberships(facts).values() for value in values}
    for fact in facts.get("network_contexts", ()):
        family = context_type(fact)
        if family not in {"vrf", "zone", "vlan"}:
            continue
        attributes = fact.get("attributes", {})
        if fact.get("fact_family") == "network_context":
            if fact["id"] not in used_contexts and family not in {"vlan", "vrf"}:
                continue
            if family != "zone" and str(attributes.get("name", "")).strip().lower() in {"default", "global"}:
                continue
        code, name = _network_context_identity(fact, attributes)
        metadata = {"network_context_type": family, "context_id": fact["id"]}
        if family == "vrf":
            metadata["vrf_id"] = str(attributes.get("local_id") or name)
        if family == "vlan":
            vlan_id = attributes.get("vlan_id") or attributes.get("local_id")
            if str(vlan_id).isdigit() and 1 <= int(vlan_id) <= 4094:
                metadata["vlan_id"] = int(vlan_id)
                if not re.search(r"(?<!\d)" + str(vlan_id) + r"(?!\d)", name):
                    name = f"{name} · VLAN {vlan_id}"
        if attributes.get("vni") is not None:
            metadata["vni"] = attributes["vni"]
        records.append({
            "code": code,
            "scope_type": "security_zone" if family == "zone" else "vrf",
            "name": name,
            "provenance_ids": fact.get("provenance_ids", ()),
            "member_id": fact["id"],
            "attributes": metadata,
        })
    return records


def _build_scope_index(records, profile, facts, revision):
    prepared = _prepare_scope_records(records)
    parent_codes = {item["code"]: item.get("parent_code") for item in prepared}
    _validate_code_tree(parent_codes)
    _validate_grouping(prepared, parent_codes, profile)
    provenance = _provenance_index(facts)
    root = _root_scope(facts, revision)
    index = {root["id"]: root}
    for item in prepared:
        parent_id = _parent_scope_id(item, prepared, profile["root_scope_seed"])
        scope = _scope_document(item, parent_id, provenance, revision)
        index[scope["id"]] = scope
    _validate_depth(index, profile.get("max_depth", 8))
    return index


def _prepare_scope_records(records):
    by_code: dict[str, dict[str, Any]] = {}
    for raw in sorted(records, key=_scope_sort_key):
        code = _required(raw, "code")
        normalized = dict(raw)
        normalized["code"] = code
        previous = by_code.get(code)
        if previous is not None and _scope_signature(previous) != _scope_signature(normalized):
            raise ScopeTreeError(f"scope {code} has multiple parents or definitions")
        by_code[code] = normalized
    return list(by_code.values())


def _scope_sort_key(item):
    return str(item.get("code")), str(item.get("scope_type"))


def _validate_code_tree(parents):
    for code in parents:
        seen: set[str] = set()
        current = code
        while current is not None:
            if current in seen:
                raise ScopeTreeError("scope cycle detected")
            seen.add(current)
            current = parents.get(current)


def _validate_grouping(records, parent_codes, profile):
    order = [_profile_scope_type(item) for item in profile["grouping_order"]]
    ranks = {item: index for index, item in enumerate(order)}
    types = {item["code"]: _normalized_scope_type(item["scope_type"]) for item in records}
    for code, parent_code in parent_codes.items():
        scope_type = types[code]
        if scope_type not in ranks:
            raise ScopeTreeError(f"scope type {scope_type} is not in profile grouping_order")
        if parent_code is None:
            continue
        if parent_code not in types:
            raise ScopeTreeError(f"scope parent {parent_code} is unknown")
        if ranks[types[parent_code]] >= ranks[scope_type]:
            raise ScopeTreeError("scope parent violates profile grouping_order")


def _validate_depth(index, max_depth):
    parents = {key: value["parent_scope_id"] for key, value in index.items()}
    for scope_id in parents:
        if len(_lineage(scope_id, parents)) - 1 > max_depth:
            raise ScopeTreeError("scope tree exceeds profile max_depth")


def _lineage(scope_id, parents):
    lineage: list[str] = []
    seen: set[str] = set()
    current = scope_id
    while current is not None:
        if current in seen:
            raise ScopeTreeError("scope cycle detected")
        if current not in parents:
            raise ScopeTreeError(f"unknown scope {current}")
        seen.add(current)
        lineage.append(current)
        current = parents[current]
    return lineage


def _root_scope(facts, revision):
    snapshot_id = str(facts.get("snapshot_id") or "unknown-snapshot")
    return {
        "id": ROOT_SCOPE_ID,
        "parent_scope_id": None,
        "scope_type": "organization",
        "name": str(facts.get("organization_name") or "Organization"),
        "summary": _empty_summary(),
        "provenance": [_derived("profile.root/v1", revision, [snapshot_id])],
    }


def _scope_document(record, parent_id, provenance, revision):
    scope_type = _normalized_scope_type(record["scope_type"])
    values = [provenance[item] for item in record.get("provenance_ids", ())
              if item in provenance]
    member_id = str(record.get("member_id") or record["code"])
    document = {
        "id": _scope_id(scope_type, record["code"]),
        "parent_scope_id": parent_id,
        "scope_type": scope_type,
        "name": str(record.get("name") or record["code"]),
        "summary": _empty_summary(),
        "provenance": values or [_derived("profile.scope/v1", revision, [member_id])],
    }
    if record.get("attributes"):
        document["attributes"] = dict(record["attributes"])
    return document


def _parent_scope_id(record, records, root_id):
    parent_code = record.get("parent_code")
    if parent_code is None:
        return root_id
    parent = next(item for item in records if item["code"] == parent_code)
    return _scope_id(_normalized_scope_type(parent["scope_type"]), parent_code)


def _finalize_scopes(index, entities):
    for scope in index.values():
        scope["summary"] = _scope_summary(scope["id"], index, entities)
    parents = {key: value["parent_scope_id"] for key, value in index.items()}
    return tuple(sorted(index.values(), key=lambda item: (len(_lineage(item["id"], parents)), item["id"])))


def _scope_summary(scope_id, scopes, entities):
    return {
        "key_device_count": sum(
            (item["scope_id"] == scope_id or scope_id in item.get("scope_memberships", ())) and item["kind"] == "device"
            and item["importance"] in {"critical", "important"} for item in entities
        ),
        "network_segment_count": sum(
            item["scope_id"] == scope_id and item["kind"] == "network_segment"
            for item in entities
        ),
        "child_scope_count": sum(
            item["parent_scope_id"] == scope_id for item in scopes.values()
        ),
        "cross_scope_relation_count": 0,
    }


def _normalize_memberships(raw_memberships, scopes):
    by_code = {item_id.split(":", 2)[-1]: item_id for item_id in scopes}
    normalized: list[str] = []
    unknown: list[str] = []
    for raw in sorted(set(str(item) for item in raw_memberships)):
        candidates = (raw, _safe(raw), _safe(_context_code(raw)))
        scope_id = raw if raw in scopes else next(
            (by_code[item] for item in candidates if item in by_code), None
        )
        if scope_id is None:
            unknown.append(raw)
        else:
            normalized.append(scope_id)
    return normalized, unknown


def _has_authoritative_conflict(scope_ids, scopes):
    types = [scopes[item]["scope_type"] for item in scope_ids]
    return len(types) != len(set(types))


def _ensure_unassigned(scopes, facts, revision):
    if UNASSIGNED_SCOPE_ID in scopes:
        return
    scopes[UNASSIGNED_SCOPE_ID] = {
        "id": UNASSIGNED_SCOPE_ID,
        "parent_scope_id": ROOT_SCOPE_ID,
        "scope_type": "custom",
        "name": "Unassigned",
        "summary": _empty_summary(),
        "provenance": [_derived(
            "profile.unassigned/v1", revision,
            [str(facts.get("snapshot_id") or "unknown-snapshot")],
        )],
    }


def _entity(identifier, scope_id, memberships, kind, name, role, representation,
            decision, attributes, provenance, *, reasons=None):
    reason_codes = reasons if reasons is not None else list(decision.reason_codes)
    return {
        "id": identifier,
        "scope_id": scope_id,
        "scope_memberships": list(memberships),
        "kind": kind,
        "name": name,
        "role": role,
        "representation": representation,
        "importance": decision.level,
        "importance_reasons": reason_codes,
        "attributes": attributes,
        "provenance": provenance,
    }


def _device_attributes(fact):
    source = fact.get("attributes", {})
    aliases = source.get("identity_aliases", ())
    serial = next((item.get("value") for item in aliases if item.get("type") == "serial"), None)
    output = {
        "management_ip": next(iter(source.get("management_ips", ())), None),
        "vendor": source.get("vendor"),
        "model": source.get("model"),
        "serial": serial,
        "location": source.get("location"),
    }
    return {key: value for key, value in output.items() if value not in (None, "")}


def _segment_virtual_addresses(segment_id, facts):
    addresses = {
        str(item.get("attributes", {}).get("virtual_address"))
        for item in facts.get("gateways", ())
        if item.get("attributes", {}).get("segment_id") == segment_id
        and item.get("attributes", {}).get("virtual_address")
    }
    return sorted(addresses)


def _entity_name(fact, fallback):
    attributes = fact.get("attributes", {})
    hostname = None
    for alias in attributes.get("identity_aliases", ()):
        if alias.get("type") != "hostname":
            continue
        value = alias.get("value")
        if isinstance(value, str) and value.strip() and value.strip().lower() != "unknown":
            hostname = value.strip()
            break
    return str(attributes.get("name") or attributes.get("hostname") or hostname or fallback)


def _provenance_index(facts):
    return {item["id"]: item["provenance"] for item in facts.get("provenance_catalog", ())}


def _matching_group_fact(group, facts):
    suffix = group.canonical_id.split(":")[-1]
    return next((key for key in facts if key.endswith(suffix)), "")


def _synthetic_group_fact(group):
    return {"id": group.canonical_id, "provenance_ids": list(group.provenance_ids)}


def _has_authoritative_mark(fact):
    attributes = fact.get("attributes", {})
    return attributes.get("important") is True or attributes.get("importance") in {
        "critical", "important"
    }


def _is_access_device(roles):
    return bool(roles.intersection({"access", "access_switch", "edge_switch"}))


def _scope_signature(record):
    return (
        _normalized_scope_type(record["scope_type"]),
        record.get("parent_code"),
        record.get("name") or record["code"],
    )


def _scope_id(scope_type, code):
    return f"scope:{scope_type}:{_safe(code)}"


def _normalized_scope_type(scope_type):
    try:
        return SCOPE_TYPES[str(scope_type)]
    except KeyError as exc:
        raise ScopeTreeError(f"unsupported scope type {scope_type}") from exc


def _profile_scope_type(scope_type):
    return _normalized_scope_type(scope_type)


def _context_code(value):
    text = str(value)
    for prefix in ("vrf:", "zone:", "scope:network_context:"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _network_context_identity(fact, attributes):
    if fact.get("fact_family") != "vlan":
        name = attributes.get("name") or attributes.get("local_id") or fact["id"]
        return _context_code(fact["id"]), name
    parts = str(fact["id"]).split(":")
    local_id = str(attributes.get("vlan_id") or attributes.get("local_id") or parts[-1])
    owner = ":".join(parts[1:-1])
    return f"{owner}:vlan-{local_id}", f"VLAN {local_id}"


def _derived(rule_id, revision, member_ids):
    return {
        "type": "derived",
        "rule_id": rule_id,
        "profile_revision": revision,
        "member_ids": sorted(set(member_ids)),
    }


def _empty_summary():
    return {
        "key_device_count": 0,
        "network_segment_count": 0,
        "child_scope_count": 0,
        "cross_scope_relation_count": 0,
    }


def _required(value, key):
    result = str(value.get(key, "")).strip()
    if not result:
        raise ScopeTreeError(f"scope record needs {key}")
    return result


def _safe(value):
    return quote(str(value).strip(), safe="-._~:")
