"""Facts to nested graph/v2 presentation document."""

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
    return graph
