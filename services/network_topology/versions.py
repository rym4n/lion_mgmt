"""Validation, hashing, and semantic graph-version differences."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from services.network_topology.canonicalization import canonicalize_json, sha256_uri
from services.network_topology.contracts import ContractError, validate_contract

FACTS_SCHEMA = "aioe.network-topology.facts/v1"
GRAPH_SCHEMA = "aioe.network-topology.graph/v2"
PROFILE_SCHEMA = "aioe.network-topology.profile/v1"
_LAYOUT_FIELDS = frozenset(
    {
        "algorithm",
        "coordinates",
        "layout",
        "layout_json",
        "orphan_items",
        "pending_item_ids",
        "pinned",
        "position",
        "viewport",
        "x",
        "y",
    }
)


class PublishingError(RuntimeError):
    """Fail-closed publication error with a stable, payload-free code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def graph_hash(graph: Mapping[str, Any]) -> str:
    return sha256_uri(canonicalize_json(graph))


def facts_hash(facts: Mapping[str, Any]) -> str:
    return sha256_uri(canonicalize_json(facts))


def validate_publication(
    facts: object,
    graph: object,
    profile: object,
    profile_revision: int,
    *,
    require_non_empty: bool = False,
) -> None:
    """Validate frozen contracts and publication-only cross-object invariants."""
    _validate_contracts(facts, graph, profile)
    if type(profile_revision) is not int or profile_revision < 1:
        raise PublishingError("PROFILE_REVISION_INVALID")
    scopes = _indexed(graph["scopes"], "SCOPE_ID_INVALID")
    entities = _indexed(graph["entities"], "ENTITY_ID_INVALID")
    relations = _indexed(graph["relations"], "RELATION_ID_INVALID")
    _validate_scope_tree(graph, profile, scopes)
    _validate_entity_references(entities, scopes)
    _validate_relation_endpoints(relations, entities, scopes)
    if graph.get("physical_topology") is not None:
        physical = graph["physical_topology"]
        physical_entities = _indexed(physical["entities"], "ENTITY_ID_INVALID")
        physical_relations = _indexed(physical["relations"], "RELATION_ID_INVALID")
        _validate_entity_references(physical_entities, scopes)
        _validate_relation_endpoints(physical_relations, physical_entities, scopes)
    _validate_graph_provenance(graph, profile_revision)
    _validate_fact_provenance(facts)
    _validate_graph_fact_references(facts, graph)
    if require_non_empty and not (entities or relations or len(scopes) > 1):
        raise PublishingError("GRAPH_EMPTY")


def _validate_contracts(facts, graph, profile):
    try:
        validate_contract(facts, FACTS_SCHEMA)
        validate_contract(graph, GRAPH_SCHEMA)
        validate_contract(profile, PROFILE_SCHEMA)
    except (ContractError, TypeError, ValueError):
        raise PublishingError("PUBLICATION_SCHEMA_INVALID") from None


def _indexed(values, code):
    result = {}
    for value in values:
        identifier = value.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise PublishingError(code)
        result[identifier] = value
    return result


def _validate_scope_tree(graph, profile, scopes):
    root_id = graph["root_scope_id"]
    root = scopes.get(root_id)
    if root is None or root.get("parent_scope_id") is not None:
        raise PublishingError("ROOT_SCOPE_INVALID")
    if profile.get("root_scope_seed") != root_id:
        raise PublishingError("PROFILE_ROOT_MISMATCH")
    for scope_id, scope in scopes.items():
        parent = scope.get("parent_scope_id")
        if scope_id != root_id and parent not in scopes:
            raise PublishingError("SCOPE_REFERENCE_MISSING")
        _walk_scope(scope_id, root_id, scopes, int(profile["max_depth"]))


def _walk_scope(scope_id, root_id, scopes, max_depth):
    seen = set()
    current = scope_id
    while current is not None:
        if current in seen:
            raise PublishingError("SCOPE_CYCLE")
        seen.add(current)
        current = scopes[current].get("parent_scope_id")
        if len(seen) > max_depth + 1:
            raise PublishingError("SCOPE_DEPTH_EXCEEDED")
    if root_id not in seen:
        raise PublishingError("SCOPE_DISCONNECTED")


def _validate_entity_references(entities, scopes):
    for entity in entities.values():
        scope_ids = [entity.get("scope_id"), *entity.get("scope_memberships", [])]
        if any(scope_id not in scopes for scope_id in scope_ids):
            raise PublishingError("ENTITY_SCOPE_REFERENCE_MISSING")


def _validate_relation_endpoints(relations, entities, scopes):
    for relation in relations.values():
        endpoints = relation.get("endpoints", [])
        valid = len(endpoints) == 2 and all(
            _valid_endpoint(item, entities, scopes) for item in endpoints
        )
        if not valid:
            raise PublishingError("RELATION_ENDPOINT_MISSING")
        owner_scope = relation.get("owner_scope_id")
        if owner_scope is not None and owner_scope not in scopes:
            raise PublishingError("RELATION_SCOPE_REFERENCE_MISSING")


def _valid_endpoint(endpoint, entities, scopes):
    references = {"entity": entities, "scope": scopes}
    collection = references.get(endpoint.get("ref_type"))
    return collection is not None and endpoint.get("ref_id") in collection


def _validate_graph_provenance(graph, profile_revision):
    for item in _graph_items(graph):
        _validate_item_provenance(item, profile_revision)


def _validate_item_provenance(item, profile_revision):
    provenance = item.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        raise PublishingError("GRAPH_PROVENANCE_MISSING")
    for entry in provenance:
        revision = entry.get("profile_revision")
        if entry.get("type") == "derived" and revision != profile_revision:
            raise PublishingError("GRAPH_PROFILE_PROVENANCE_MISMATCH")


def _validate_fact_provenance(facts):
    catalog = facts.get("provenance_catalog", [])
    identifiers = {
        item.get("id") for item in catalog if isinstance(item, Mapping) and item.get("id")
    }
    for collection, values in facts.items():
        if collection in {"schema_version", "snapshot_id", "analysis_run_id", "provenance_catalog", "unresolved_references"}:
            continue
        for value in values if isinstance(values, list) else ():
            refs = _fact_provenance_refs(value)
            if not refs or not refs.issubset(identifiers):
                raise PublishingError("FACT_PROVENANCE_REFERENCE_MISSING")


def _fact_provenance_refs(value):
    refs = value.get(
        "provenance_ids",
        value.get("provenance_refs", value.get("provenance_ref", ())),
    )
    if isinstance(refs, str):
        return {refs}
    if isinstance(refs, list):
        return {item for item in refs if isinstance(item, str)}
    return set()


def _validate_graph_fact_references(facts, graph):
    allowed = {facts["snapshot_id"], facts["analysis_run_id"]}
    for values in facts.values():
        if isinstance(values, list):
            allowed.update(
                item.get("id") for item in values
                if isinstance(item, Mapping) and isinstance(item.get("id"), str)
            )
    for item in _graph_items(graph):
        _validate_member_references(item, allowed)


def _graph_items(graph):
    for collection in ("scopes", "entities", "relations"):
        for item in graph.get(collection, ()):
            yield item
            yield from item.get("attributes", {}).get("interfaces", ())
    if graph.get("physical_topology") is not None:
        yield from _graph_items(graph["physical_topology"])


def _validate_member_references(item, allowed):
    if any(member not in allowed for member in item.get("member_fact_ids", ())):
        raise PublishingError("GRAPH_PROVENANCE_REFERENCE_MISSING")
    for provenance in item["provenance"]:
        members = provenance.get("member_ids", [])
        if any(member not in allowed for member in members):
            raise PublishingError("GRAPH_PROVENANCE_REFERENCE_MISSING")


def build_graph_diff(before: object, after: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize semantic changes by scope without comparing layout fields."""
    old_graph = before if isinstance(before, Mapping) else _empty_graph(after)
    old = _classified(old_graph)
    new = _classified(after)
    scope_ids = sorted(set(old) | set(new))
    scopes = {}
    totals = {"added": 0, "removed": 0, "modified": 0}
    for scope_id in scope_ids:
        summary = _scope_diff(old.get(scope_id, {}), new.get(scope_id, {}))
        if _has_changes(summary):
            scopes[scope_id] = summary
            _accumulate_totals(totals, summary)
    return {"schema_version": "aioe.network-topology.graph-diff/v1", "totals": totals, "scopes": scopes}


def _empty_graph(graph):
    return {"root_scope_id": graph.get("root_scope_id"), "scopes": [], "entities": [], "relations": []}


def _classified(graph):
    output = defaultdict(lambda: defaultdict(dict))
    entities = {item["id"]: item for item in graph.get("entities", [])}
    for scope in graph.get("scopes", []):
        owner = scope.get("parent_scope_id") or scope["id"]
        output[owner]["scopes"][scope["id"]] = _semantic(scope)
    for entity in entities.values():
        category = _entity_category(entity)
        if category:
            output[entity["scope_id"]][category][entity["id"]] = _semantic(entity)
    for relation in graph.get("relations", []):
        if _is_cross_scope(relation, entities):
            owner = relation.get("owner_scope_id") or graph.get("root_scope_id")
            output[owner]["cross_scope_relations"][relation["id"]] = _semantic(relation)
    return output


def _entity_category(entity):
    if entity.get("kind") in {"network_segment", "segment", "subnet"}:
        return "segments"
    if entity.get("importance") in {"critical", "important"}:
        return "key_devices"
    return None


def _is_cross_scope(relation, entities):
    scopes = set()
    for endpoint in relation.get("endpoints", []):
        ref = endpoint.get("ref_id")
        if endpoint.get("ref_type") == "scope":
            scopes.add(ref)
        elif ref in entities:
            scopes.add(entities[ref].get("scope_id"))
    return len(scopes) > 1


def _semantic(value):
    return _without_layout(deepcopy(value))


def _without_layout(value):
    if isinstance(value, dict):
        return {key: _without_layout(item) for key, item in value.items() if key not in _LAYOUT_FIELDS}
    if isinstance(value, list):
        return [_without_layout(item) for item in value]
    return value


def _scope_diff(old, new):
    return {
        category: _mapping_diff(old.get(category, {}), new.get(category, {}))
        for category in ("scopes", "key_devices", "segments", "cross_scope_relations")
    }


def _mapping_diff(old, new):
    old_ids, new_ids = set(old), set(new)
    return {
        "added": sorted(new_ids - old_ids),
        "removed": sorted(old_ids - new_ids),
        "modified": sorted(key for key in old_ids & new_ids if old[key] != new[key]),
    }


def _has_changes(summary):
    return any(values for category in summary.values() for values in category.values())


def _accumulate_totals(totals, summary):
    for category in summary.values():
        for change, values in category.items():
            totals[change] += len(values)


def scope_items(graph: Mapping[str, Any]) -> dict[str, set[str]]:
    result = {scope["id"]: set() for scope in graph["scopes"]}
    for scope in graph["scopes"]:
        parent = scope.get("parent_scope_id")
        if parent in result:
            result[parent].add(scope["id"])
    for entity in graph["entities"]:
        result[entity["scope_id"]].add(entity["id"])
    return result


def compact_change_summary(diff: Mapping[str, Any]) -> str:
    return canonicalize_json(diff["totals"]).decode()
