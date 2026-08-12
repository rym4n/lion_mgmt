from __future__ import annotations

from typing import Any


def scope_parent_index(graph: dict[str, Any]) -> dict[str, str | None]:
    parents = {item["id"]: item["parent_scope_id"] for item in graph["scopes"]}
    assert len(parents) == len(graph["scopes"])
    return parents


def scope_ancestry(scope_id: str, parents: dict[str, str | None]) -> list[str]:
    ancestry: list[str] = []
    seen: set[str] = set()
    current: str | None = scope_id
    while current is not None:
        assert current in parents
        assert current not in seen
        ancestry.append(current)
        seen.add(current)
        current = parents[current]
    return ancestry


def lowest_common_ancestor(scope_ids: list[str], parents: dict[str, str | None]) -> str:
    assert scope_ids
    lineages = [scope_ancestry(scope_id, parents) for scope_id in scope_ids]
    common = set(lineages[0]).intersection(*(set(item) for item in lineages[1:]))
    assert common
    return max(common, key=lambda item: len(scope_ancestry(item, parents)))


def endpoint_primary_scope(
    endpoint: dict[str, Any], entities: dict[str, dict[str, Any]]
) -> str:
    if endpoint["ref_type"] == "scope":
        return endpoint["ref_id"]
    return entities[endpoint["ref_id"]]["scope_id"]


def recompute_scope_summary(graph: dict[str, Any], scope_id: str) -> dict[str, int]:
    entities = {item["id"]: item for item in graph["entities"]}
    return {
        "key_device_count": sum(
            item["scope_id"] == scope_id
            and item["kind"] == "device"
            and item["importance"] in {"critical", "important"}
            for item in graph["entities"]
        ),
        "network_segment_count": sum(
            item["scope_id"] == scope_id and item["kind"] == "network_segment"
            for item in graph["entities"]
        ),
        "child_scope_count": sum(
            item["parent_scope_id"] == scope_id for item in graph["scopes"]
        ),
        "cross_scope_relation_count": sum(
            item["owner_scope_id"] == scope_id
            and len(
                {
                    endpoint_primary_scope(endpoint, entities)
                    for endpoint in item["endpoints"]
                }
            )
            > 1
            for item in graph["relations"]
        ),
    }


def assert_scope_summaries(case: dict[str, Any]) -> None:
    assert case["scenario_expectations"]["scope_summary_semantics"] == {
        "key_device_count": (
            "critical or important device entities with this primary scope"
        ),
        "network_segment_count": "network-segment entities with this primary scope",
        "child_scope_count": "direct child scopes",
        "cross_scope_relation_count": (
            "relations owned by this scope whose endpoint primary scopes differ"
        ),
    }
    graph = case["expected_presentation_graph"]
    for scope in graph["scopes"]:
        assert scope["summary"] == recompute_scope_summary(graph, scope["id"])


def assert_scope_tree_integrity(graph: dict[str, Any]) -> None:
    parents = scope_parent_index(graph)
    roots = [scope_id for scope_id, parent_id in parents.items() if parent_id is None]
    assert roots == [graph["root_scope_id"]]
    for parent_id in parents.values():
        assert parent_id is None or parent_id in parents
    for scope_id in parents:
        assert scope_ancestry(scope_id, parents)[-1] == graph["root_scope_id"]

    entities = {item["id"]: item for item in graph["entities"]}
    for entity in entities.values():
        memberships = entity["scope_memberships"]
        if len(memberships) > 1:
            assert entity["scope_id"] == lowest_common_ancestor(memberships, parents)
    for relation in graph["relations"]:
        endpoint_scopes = [
            endpoint_primary_scope(endpoint, entities)
            for endpoint in relation["endpoints"]
        ]
        assert relation["owner_scope_id"] == lowest_common_ancestor(
            endpoint_scopes, parents
        )
