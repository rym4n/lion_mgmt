"""Facts to nested graph/v2 presentation document."""

from ipaddress import ip_address
from typing import Any, Mapping

from services.network_topology.relationships import build_relationships
from services.network_topology.scope_tree import build_scope_tree


def authoritative_inputs(collection: Mapping[str, Any]) -> tuple[list[dict], dict[str, list[str]]]:
    scopes = list((collection.get("authoritative_context") or {}).get("scopes") or [])
    memberships = {}
    for source in collection.get("sources", []):
        for resource in source.get("resources", []):
            scope_ids = list((resource.get("asset_context") or {}).get("scope_ids") or [])
            if scope_ids:
                memberships[f"device:{resource['resource_id']}"] = scope_ids
    return scopes, memberships


def build_graph(facts, resolution, profile, *, profile_revision: int, collection) -> dict[str, Any]:
    scopes, memberships = authoritative_inputs(collection)
    tree = build_scope_tree(
        facts, resolution, profile, authoritative_scopes=scopes,
        entity_memberships=memberships, profile_revision=profile_revision,
    )
    relations = build_relationships(facts, resolution, tree, profile_revision=profile_revision)
    graph = tree.as_graph()
    graph["relations"] = [dict(item) for item in relations.relations]
    _apply_collection_display(graph, facts, resolution, collection, profile_revision)
    return graph


def _apply_collection_display(graph, facts, resolution, collection, profile_revision):
    """Fill presentation gaps from this immutable snapshot's bound resource.

    asset_context has no formal authoritative provenance contract and device
    facts do not permit a name field. Keep it out of Facts/identity resolution;
    record a derived presentation rule referencing the existing snapshot and
    device fact instead of inventing observed or CMDB evidence.
    """
    snapshot_id = facts.get("snapshot_id")
    if not snapshot_id or collection.get("snapshot_id") != snapshot_id:
        return
    resources = {}
    duplicates = set()
    for source in collection.get("sources", ()):
        for resource in source.get("resources", ()):
            resource_id = resource.get("resource_id")
            if resource_id in resources:
                duplicates.add(resource_id)
            resources[resource_id] = resource
    entities = {item["id"]: item for item in graph["entities"]}
    for fact in facts.get("devices", ()):
        resource_ids = fact.get("resource_ids", ())
        if len(resource_ids) != 1:
            continue
        resource_id = resource_ids[0]
        if (resource_id in duplicates or fact["id"] != f"device:{resource_id}"
                or fact.get("attributes", {}).get("inventory_state") != "managed"):
            continue
        resource = resources.get(resource_id)
        entity = entities.get(resolution.fact_identity_map.get(fact["id"]))
        if resource is None or entity is None or entity["kind"] != "device":
            continue
        context = resource.get("asset_context") or {}
        changed = False
        name = _display_text(context.get("name"))
        if entity["name"] == entity["id"] and name:
            entity["name"] = name
            changed = True
        attributes = entity["attributes"]
        vendor = _display_text(context.get("vendor"))
        if "vendor" not in attributes and vendor:
            attributes["vendor"] = vendor
            changed = True
        if "management_ip" not in attributes:
            address = _management_ip(context.get("management_ips", ()))
            if address:
                attributes["management_ip"] = address
                changed = True
        if changed:
            entity["provenance"].append({
                "type": "derived", "rule_id": "collection.asset_display/v1",
                "profile_revision": profile_revision,
                "member_ids": sorted([snapshot_id, fact["id"]]),
            })


def _display_text(value):
    if isinstance(value, str):
        text = value.strip()
        if text and text.lower() != "unknown":
            return text
    return None


def _management_ip(values):
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            return str(ip_address(value.strip()))
        except ValueError:
            continue
    return None
