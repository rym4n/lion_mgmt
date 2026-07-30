from __future__ import annotations

from typing import Any

from tests.network_topology.test_phase0_golden_fixtures import (
    assert_provenance_resolves,
    authoritative_index,
    fact_index,
    observation_index,
)


def collection_observation(
    case: dict[str, Any], resource_id: str, kind: str
) -> dict[str, Any]:
    return next(
        item
        for source in case["collection"]["sources"]
        for resource in source["resources"]
        if resource["resource_id"] == resource_id
        for item in resource["result"]["observations"]
        if item["kind"] == kind
    )


def resolved_fact_provenances(case: dict[str, Any], item: dict[str, Any]) -> list[Any]:
    catalog = {
        entry["id"]: entry["provenance"]
        for entry in case["expected_facts"]["provenance_catalog"]
    }
    observations = observation_index(case["collection"])
    authoritative = authoritative_index(case["collection"])
    return [
        assert_provenance_resolves(catalog[item_id], observations, authoritative)
        for item_id in item["provenance_ids"]
    ]


def assert_parallel_trunk_evidence(case: dict[str, Any]) -> None:
    expected = case["scenario_expectations"]["parallel_evidence"]["trunk"]
    relations = {
        item["id"]: item for item in case["expected_presentation_graph"]["relations"]
    }
    relation = relations[expected["relation_id"]]
    facts = fact_index(case["expected_facts"])
    assert relation["multi_edge_identity"]["attachment_key"] == "|".join(
        expected["attachment_interfaces"]
    )
    assert relation["multi_edge_identity"]["context_key"] == expected["context_id"]
    assert_trunk_context(facts, expected)
    member_ids: list[str] = []
    for side in expected["sides"]:
        assert_lag_side(case, facts, side)
        assert_trunk_adjacency(case, facts, side, expected)
        member_ids.extend(
            [
                side["adjacency_fact_id"],
                side["member_fact_id"],
                side["lag_fact_id"],
                side["membership_fact_id"],
            ]
        )
    assert relation["member_fact_ids"] == member_ids


def assert_trunk_context(
    facts: dict[str, dict[str, Any]], expected: dict[str, Any]
) -> None:
    assert expected["context_id"] in facts
    context = facts[expected["context_id"]]
    attributes = context["attributes"]
    members = [facts[side["member_fact_id"]] for side in expected["sides"]]
    member_by_device = {member["attributes"]["device_id"]: member for member in members}
    assert context["fact_family"] in {"vlan", "network_context"}
    assert attributes["vlan_id"] == expected["vlan_id"]
    assert all(
        expected["vlan_id"] in member["attributes"]["vlan_ids"] for member in members
    )
    assert attributes["owner_id"] in member_by_device
    owner_member = member_by_device[attributes["owner_id"]]
    assert set(context["resource_ids"]) == set(owner_member["resource_ids"])
    interface_ids = attributes.get("interface_ids", [])
    assert not interface_ids or owner_member["id"] in interface_ids


def assert_lag_side(
    case: dict[str, Any], facts: dict[str, dict[str, Any]], side: dict[str, Any]
) -> None:
    required = {
        side["lag_fact_id"],
        side["member_fact_id"],
        side["membership_fact_id"],
    }
    assert required <= set(facts)
    lag = facts[side["lag_fact_id"]]
    member = facts[side["member_fact_id"]]
    membership = facts[side["membership_fact_id"]]
    assert lag["resource_ids"] == member["resource_ids"] == [side["resource_id"]]
    assert membership["resource_ids"] == [side["resource_id"]]
    assert membership["attributes"]["member_id"] == member["id"]
    assert membership["attributes"]["container_id"] == lag["id"]
    assert member["attributes"]["lag_id"] == lag["id"]
    observation = collection_observation(
        case, side["resource_id"], "l2.link_aggregation"
    )
    aggregations = [
        item
        for item in observation["data"]["aggregations"]
        if item["interface"] == lag["attributes"]["canonical_name"]
    ]
    assert len(aggregations) == 1
    assert aggregations[0]["members"] == [member["attributes"]["canonical_name"]]


def assert_trunk_adjacency(
    case: dict[str, Any],
    facts: dict[str, dict[str, Any]],
    side: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    assert side["adjacency_fact_id"] in facts
    adjacency = facts[side["adjacency_fact_id"]]
    local = facts[side["member_fact_id"]]
    remote = facts[side["remote_member_fact_id"]]
    attributes = adjacency["attributes"]
    assert adjacency["resource_ids"] == [side["resource_id"]]
    assert attributes["local_id"] == local["id"]
    assert attributes["remote_id"] == side["remote_device_fact_id"]
    assert remote["attributes"]["device_id"] == side["remote_device_fact_id"]
    local_name = local["attributes"]["canonical_name"]
    remote_name = remote["attributes"]["canonical_name"]
    assert attributes["attachments"] == [local_name, remote_name]
    assert local["attributes"]["vlan_ids"] == remote["attributes"]["vlan_ids"]
    assert local["attributes"]["vlan_ids"] == [expected["vlan_id"]]
    assert attributes["context_id"] == expected["context_id"]
    observation = collection_observation(case, side["resource_id"], "l2.neighbors")
    matches = [
        item
        for item in observation["data"]["neighbors"]
        if item["local_interface"] == local_name
    ]
    assert len(matches) == 1
    neighbor = matches[0]
    remote_resource = facts[side["remote_device_fact_id"]]["resource_ids"][0]
    identity = collection_observation(case, remote_resource, "system.identity")["data"]
    assert neighbor["remote_chassis_id"] == identity["chassis_id"]
    assert neighbor["remote_system_name"] == identity["hostname"]
    assert neighbor["remote_port_id"] == remote_name
    assert adjacency["provenance_ids"] == side["neighbor_provenance_ids"]
    assert resolved_fact_provenances(case, adjacency) == [
        identity["chassis_id"],
        remote_name,
    ]
