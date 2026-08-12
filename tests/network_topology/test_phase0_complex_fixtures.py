from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
import pytest

from services.network_topology import contracts
from services.network_topology.canonicalization import (
    canonicalize_and_hash_snapshot,
    canonicalize_json,
    sha256_uri,
    validate_observation_hashes,
)
from tests.network_topology.complex_fixture_assertions import (
    assert_parallel_trunk_evidence,
)
from tests.network_topology.scope_assertions import (
    assert_scope_summaries,
    assert_scope_tree_integrity,
)
from tests.network_topology.test_phase0_golden_fixtures import (
    assert_case_output_binding,
    assert_derived_member_integrity,
    assert_fact_reference_integrity,
    assert_graph_reference_integrity,
    assert_manifest_profile_closure,
    assert_no_unresolved_graph,
    assert_provenance_resolves,
    assert_provenance_integrity,
    authoritative_index,
    fact_index,
    iter_observations,
    observation_index,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "network_topology"
    / "phase0_task04"
    / "complex-scenarios-v1.json"
)
GROUP_TYPES = {
    "ha": "ha",
    "stack": "stack",
    "irf": "irf",
    "vss": "vss",
    "mlag": "mlag",
    "f5_cluster": "cluster",
}


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def graph_index(graph: dict[str, Any], field: str) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in graph[field]}


def multi_edge_key(relation: dict[str, Any]) -> tuple[str, ...]:
    identity = relation["multi_edge_identity"]
    return tuple(
        identity[field]
        for field in (
            "endpoint_key",
            "layer",
            "relation_type",
            "attachment_key",
            "context_key",
        )
    )


def assert_unique_multi_edge_identities(graph: dict[str, Any]) -> None:
    identities = [multi_edge_key(item) for item in graph["relations"]]
    assert len(identities) == len(set(identities))


def assert_logical_group_projection(case: dict[str, Any]) -> None:
    expected = case["scenario_expectations"]
    facts = fact_index(case["expected_facts"])
    catalog = {
        item["id"]: item["provenance"]
        for item in case["expected_facts"]["provenance_catalog"]
    }
    graph = case["expected_presentation_graph"]
    entities = graph_index(graph, "entities")
    endpoint_ids = {
        endpoint["ref_id"]
        for relation in graph["relations"]
        for endpoint in relation["endpoints"]
    }
    for label, group_type in GROUP_TYPES.items():
        group_id = expected["logical_groups"][label]
        group = facts[group_id]
        members = group["attributes"]["member_ids"]
        assert group["attributes"]["group_type"] == group_type
        assert members == expected["logical_group_members"][group_id]
        assert set(members) <= set(facts)
        assert all(facts[member]["provenance_ids"] for member in members)
        assert group_id in entities
        assert entities[group_id]["role"] == f"logical_{group_type}"
        assert entities[group_id]["provenance"] == [
            catalog[item] for item in group["provenance_ids"]
        ]
        assert set(members).isdisjoint(entities)
        assert set(members).isdisjoint(endpoint_ids)


def assert_overlap_projection(case: dict[str, Any]) -> None:
    expected = case["scenario_expectations"]
    facts = fact_index(case["expected_facts"])
    entities = graph_index(case["expected_presentation_graph"], "entities")
    network_ids = expected["overlapping_network_ids"]
    networks = [facts[item_id]["attributes"] for item_id in network_ids]
    projected = [entities[item_id] for item_id in network_ids]
    assert {item["canonical_cidr"] for item in networks} == {"198.51.100.64/26"}
    assert len({item["network_domain_id"] for item in networks}) == 2
    assert len({item["vrf_id"] for item in networks}) == 2
    assert len({item["scope_id"] for item in projected}) == 2
    for item_id, entity in zip(network_ids, projected, strict=True):
        assert entity["scope_id"] in facts[item_id]["attributes"]["scope_ids"]


def assert_firewall_scope_refs(case: dict[str, Any]) -> None:
    expected = case["scenario_expectations"]
    graph = case["expected_presentation_graph"]
    firewall_id = expected["multi_scope_firewall_id"]
    firewall = graph_index(graph, "entities")[firewall_id]
    entity_refs = expected["firewall_entity_refs"]
    validate_entity_refs(entity_refs)
    assert len({item["id"] for item in entity_refs}) == len(entity_refs)
    assert {item["canonical_entity_id"] for item in entity_refs} == {firewall_id}
    assert {item["scope_id"] for item in entity_refs} == set(
        firewall["scope_memberships"]
    )


def assert_evidence_expectation(case: dict[str, Any], item: dict[str, Any]) -> None:
    facts = fact_index(case["expected_facts"])
    fact_item = facts[item["fact_id"]]
    catalog = {
        entry["id"]: entry["provenance"]
        for entry in case["expected_facts"]["provenance_catalog"]
    }
    observations = observation_index(case["collection"])
    authoritative = authoritative_index(case["collection"])
    assert fact_item["resource_ids"] == item["resource_ids"]
    assert set(fact_item["provenance_ids"]) == set(item["values"])
    for provenance_id, expected_value in item["values"].items():
        provenance = catalog[provenance_id]
        assert provenance["resource_id"] in fact_item["resource_ids"]
        value = assert_provenance_resolves(provenance, observations, authoritative)
        assert value == expected_value


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


def logical_group_for_member(case: dict[str, Any], member_id: str) -> str:
    groups = case["scenario_expectations"]["logical_group_members"]
    matches = [group_id for group_id, members in groups.items() if member_id in members]
    assert len(matches) == 1
    return matches[0]


def assert_parallel_physical_evidence(case: dict[str, Any]) -> None:
    expected = case["scenario_expectations"]["parallel_evidence"]
    physical_expected = expected["physical"]
    relation_item = graph_index(case["expected_presentation_graph"], "relations")[
        physical_expected["relation_id"]
    ]
    facts = fact_index(case["expected_facts"])
    assert {item["ref_id"] for item in relation_item["endpoints"]} == set(
        expected["endpoints"]
    )
    assert relation_item["member_fact_ids"] == physical_expected["adjacency_fact_ids"]
    assert relation_item["multi_edge_identity"]["attachment_key"] == "|".join(
        physical_expected["attachment_interfaces"]
    )
    directions: list[tuple[str, str]] = []
    for fact_id in physical_expected["adjacency_fact_ids"]:
        directions.append(
            assert_resolved_adjacency(
                case,
                facts[fact_id],
                expected["endpoints"],
                physical_expected["attachment_interfaces"],
            )
        )
    assert set(directions) == {
        (expected["endpoints"][0], expected["endpoints"][1]),
        (expected["endpoints"][1], expected["endpoints"][0]),
    }


def assert_resolved_adjacency(
    case: dict[str, Any],
    adjacency: dict[str, Any],
    endpoint_groups: list[str],
    attachment_interfaces: list[str],
) -> tuple[str, str]:
    facts = fact_index(case["expected_facts"])
    attributes = adjacency["attributes"]
    local_device = facts[attributes["local_id"]]["attributes"]["device_id"]
    remote_device = attributes["remote_id"]
    assert not remote_device.startswith("unresolved:")
    local_group = logical_group_for_member(case, local_device)
    remote_group = logical_group_for_member(case, remote_device)
    assert {local_group, remote_group} == set(endpoint_groups)
    assert attributes["attachments"] == attachment_interfaces
    remote_resource = facts[remote_device]["resource_ids"][0]
    identity = collection_observation(case, remote_resource, "system.identity")
    assert resolved_fact_provenance(case, adjacency) == identity["data"]["chassis_id"]
    return local_group, remote_group


def resolved_fact_provenance(case: dict[str, Any], item: dict[str, Any]) -> Any:
    catalog = {
        entry["id"]: entry["provenance"]
        for entry in case["expected_facts"]["provenance_catalog"]
    }
    provenance = catalog[item["provenance_ids"][0]]
    return assert_provenance_resolves(
        provenance,
        observation_index(case["collection"]),
        authoritative_index(case["collection"]),
    )


def assert_access_chain_integrity(case: dict[str, Any]) -> None:
    expected = case["scenario_expectations"]
    access_path = expected["access_path"]
    facts = fact_index(case["expected_facts"])
    relations = graph_index(case["expected_presentation_graph"], "relations")
    collapsed = relations[expected["collapsed_relation_id"]]
    assert collapsed["relation_type"] == "collapsed_path"
    assert collapsed["member_relation_ids"] == []
    assert {item["ref_id"] for item in collapsed["endpoints"]} == set(
        access_path["endpoints"]
    )
    interface_ids = [item["interface_fact_id"] for item in access_path["legs"]]
    assert len(interface_ids) == len(set(interface_ids)) == 2
    remote_groups: list[str] = []
    ordered_ids = [expected["collapsed_fact_id"]]
    for leg in access_path["legs"]:
        interface = facts[leg["interface_fact_id"]]
        adjacency = facts[leg["adjacency_fact_id"]]
        assert interface["attributes"]["device_id"] == expected["collapsed_fact_id"]
        assert adjacency["attributes"]["local_id"] == interface["id"]
        remote_group = logical_group_for_member(
            case, adjacency["attributes"]["remote_id"]
        )
        assert remote_group == leg["remote_group_id"]
        remote_groups.append(remote_group)
        ordered_ids.extend([interface["id"], adjacency["id"]])
    assert len(set(remote_groups)) == 2
    assert set(remote_groups) == set(access_path["endpoints"])
    adjacency_ids = [item["adjacency_fact_id"] for item in access_path["legs"]]
    assert ordered_ids == access_path["ordered_fact_ids"]
    assert collapsed["member_fact_ids"] == ordered_ids
    assert collapsed["attributes"]["ordered_member_fact_ids"] == ordered_ids
    duplicates = [
        item["id"]
        for item in case["expected_presentation_graph"]["relations"]
        if {endpoint["ref_id"] for endpoint in item["endpoints"]}
        == set(access_path["endpoints"])
        and set(adjacency_ids) <= set(item["member_fact_ids"])
    ]
    assert duplicates == [collapsed["id"]]


def assert_complete_expectation_tables(case: dict[str, Any]) -> None:
    unresolved = [
        {"id": item["id"], "reason_code": item["reason_code"]}
        for item in case["expected_facts"]["unresolved_references"]
    ]
    assert len({item["id"] for item in unresolved}) == len(unresolved)
    assert unresolved == case["expected_unresolved_references"]
    catalog = {
        item["id"]: item["provenance"]
        for item in case["expected_facts"]["provenance_catalog"]
    }
    expected = {
        item["provenance_id"]: item["expected_value"]
        for item in case["provenance_expectations"]
    }
    assert len(expected) == len(case["provenance_expectations"])
    assert set(catalog) == set(expected)
    observations = observation_index(case["collection"])
    authoritative = authoritative_index(case["collection"])
    for provenance_id, provenance in catalog.items():
        assert (
            assert_provenance_resolves(provenance, observations, authoritative)
            == expected[provenance_id]
        )


def test_complex_fixture_contracts_and_frozen_hashes() -> None:
    case = load_fixture()
    assert case["fixture_version"] == "aioe.network-topology.complex-golden/v1"
    assert case["synthetic_redacted"] is True
    contracts.validate_contract(
        case["collection"], "aioe.network-topology.collection/v2"
    )
    contracts.validate_contract(
        case["expected_facts"], "aioe.network-topology.facts/v1"
    )
    contracts.validate_contract(
        case["expected_presentation_graph"], "aioe.network-topology.graph/v2"
    )
    for item in iter_observations(case["collection"]):
        validate_observation_hashes(item)
    assert_frozen_hashes(case)


def assert_frozen_hashes(case: dict[str, Any]) -> None:
    hashes = case["canonical_hashes"]
    assert (
        canonicalize_and_hash_snapshot(case["collection"]).payload_hash
        == hashes["snapshot"]
    )
    assert sha256_uri(canonicalize_json(case["expected_facts"])) == hashes["facts"]
    graph_bytes = canonicalize_json(case["expected_presentation_graph"])
    assert sha256_uri(graph_bytes) == hashes["graph"]


def test_complex_fixture_reference_and_provenance_closure() -> None:
    case = load_fixture()
    facts = case["expected_facts"]
    graph = case["expected_presentation_graph"]
    assert_manifest_profile_closure(case["collection"])
    assert_case_output_binding(case)
    assert_fact_reference_integrity(facts)
    assert_graph_reference_integrity(graph, facts)
    assert_derived_member_integrity(case)
    assert_provenance_integrity(case)
    assert_no_unresolved_graph(graph)
    assert_unique_multi_edge_identities(graph)
    assert_complete_expectation_tables(case)
    assert_scope_summaries(case)


def test_logical_groups_preserve_traceable_physical_members() -> None:
    assert_logical_group_projection(load_fixture())


@pytest.mark.parametrize("mutation", ["member_reintroduced", "logical_omitted"])
def test_logical_group_projection_mutations_fail_closed(mutation: str) -> None:
    case = load_fixture()
    group_id = case["scenario_expectations"]["logical_groups"]["stack"]
    member_id = case["scenario_expectations"]["logical_group_members"][group_id][0]
    entities = case["expected_presentation_graph"]["entities"]
    if mutation == "member_reintroduced":
        member = deepcopy(next(item for item in entities if item["id"] == group_id))
        member["id"] = member_id
        entities.append(member)
    else:
        entities[:] = [item for item in entities if item["id"] != group_id]

    with pytest.raises((AssertionError, KeyError)):
        assert_logical_group_projection(case)


def test_overlapping_cidrs_remain_separate_across_vrfs_and_domains() -> None:
    case = load_fixture()
    assert_overlap_projection(case)
    facts = fact_index(case["expected_facts"])
    assert set(case["scenario_expectations"]["vrf_ids"]) <= set(facts)


@pytest.mark.parametrize("mutation", ["drop", "merge_scope"])
def test_overlap_projection_mutations_fail_closed(mutation: str) -> None:
    case = load_fixture()
    ids = case["scenario_expectations"]["overlapping_network_ids"]
    entities = case["expected_presentation_graph"]["entities"]
    if mutation == "drop":
        entities[:] = [item for item in entities if item["id"] != ids[1]]
    else:
        indexed = graph_index(case["expected_presentation_graph"], "entities")
        indexed[ids[1]]["scope_id"] = indexed[ids[0]]["scope_id"]

    with pytest.raises((AssertionError, KeyError)):
        assert_overlap_projection(case)


def test_parallel_relations_have_distinct_multi_edge_identities() -> None:
    case = load_fixture()
    graph = case["expected_presentation_graph"]
    relations = graph_index(graph, "relations")
    ids = case["scenario_expectations"]["parallel_relation_ids"]
    parallel = [relations[item_id] for item_id in ids]
    endpoint_sets = [
        {endpoint["ref_id"] for endpoint in item["endpoints"]} for item in parallel
    ]
    assert endpoint_sets[0] == endpoint_sets[1]
    assert {item["relation_type"] for item in parallel} == {
        "physical_link",
        "l2_trunk",
    }
    assert len({multi_edge_key(item) for item in parallel}) == 2
    assert_parallel_physical_evidence(case)
    assert_parallel_trunk_evidence(case)


@pytest.mark.parametrize(
    "mutation",
    ["unresolved_remote", "mismatched_remote", "missing_lag", "empty_lag"],
)
def test_parallel_evidence_mutations_fail_closed(mutation: str) -> None:
    case = load_fixture()
    physical = case["scenario_expectations"]["parallel_evidence"]["physical"]
    facts = fact_index(case["expected_facts"])
    match mutation:
        case "unresolved_remote":
            facts[physical["adjacency_fact_ids"][0]]["attributes"]["remote_id"] = (
                "unresolved:device:remote"
            )
        case "mismatched_remote":
            facts[physical["adjacency_fact_ids"][0]]["attributes"]["remote_id"] = (
                "device:cisco-edge-1"
            )
        case "missing_lag":
            lag_id = case["scenario_expectations"]["parallel_evidence"]["trunk"][
                "sides"
            ][0]["lag_fact_id"]
            case["expected_facts"]["interfaces"] = [
                item
                for item in case["expected_facts"]["interfaces"]
                if item["id"] != lag_id
            ]
        case "empty_lag":
            side = case["scenario_expectations"]["parallel_evidence"]["trunk"]["sides"][
                0
            ]
            observation = collection_observation(
                case, side["resource_id"], "l2.link_aggregation"
            )
            observation["data"]["aggregations"] = []

    with pytest.raises(AssertionError):
        if mutation in {"unresolved_remote", "mismatched_remote"}:
            assert_parallel_physical_evidence(case)
        else:
            assert_parallel_trunk_evidence(case)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_adjacency",
        "wrong_remote_device",
        "wrong_remote_port",
        "unrelated_interface",
        "wrong_vlan",
        "wrong_context",
    ],
)
def test_trunk_connecting_evidence_mutations_fail_closed(mutation: str) -> None:
    case = load_fixture()
    expected = case["scenario_expectations"]["parallel_evidence"]["trunk"]
    side = expected["sides"][0]
    facts = fact_index(case["expected_facts"])
    adjacency = facts[side["adjacency_fact_id"]]
    match mutation:
        case "missing_adjacency":
            case["expected_facts"]["adjacencies"] = [
                item
                for item in case["expected_facts"]["adjacencies"]
                if item["id"] != adjacency["id"]
            ]
        case "wrong_remote_device":
            adjacency["attributes"]["remote_id"] = "device:cisco-edge-1"
        case "wrong_remote_port":
            observation = collection_observation(
                case, side["resource_id"], "l2.neighbors"
            )
            observation["data"]["neighbors"][1]["remote_port_id"] = "wrong-port"
        case "unrelated_interface":
            adjacency["attributes"]["local_id"] = "interface:huawei-core-1:ge0-0-1"
        case "wrong_vlan":
            facts[side["remote_member_fact_id"]]["attributes"]["vlan_ids"] = [999]
        case "wrong_context":
            adjacency["attributes"]["context_id"] = "vlan:huawei-core-1:100"

    with pytest.raises(AssertionError):
        assert_parallel_trunk_evidence(case)


def test_trunk_incompatible_resolved_context_fails_closed() -> None:
    case = load_fixture()
    expected = case["scenario_expectations"]["parallel_evidence"]["trunk"]
    facts = fact_index(case["expected_facts"])
    relation = graph_index(case["expected_presentation_graph"], "relations")[
        expected["relation_id"]
    ]
    incompatible_context_id = "vlan:huawei-core-1:100"
    expected["context_id"] = incompatible_context_id
    relation["multi_edge_identity"]["context_key"] = incompatible_context_id
    for side in expected["sides"]:
        facts[side["adjacency_fact_id"]]["attributes"]["context_id"] = (
            incompatible_context_id
        )
    assert relation["multi_edge_identity"]["context_key"] == expected["context_id"]
    assert {
        facts[side["adjacency_fact_id"]]["attributes"]["context_id"]
        for side in expected["sides"]
    } == {expected["context_id"]}

    with pytest.raises(AssertionError):
        assert_parallel_trunk_evidence(case)


def test_duplicate_multi_edge_identity_fails_closed() -> None:
    graph = load_fixture()["expected_presentation_graph"]
    graph["relations"][1]["multi_edge_identity"] = deepcopy(
        graph["relations"][0]["multi_edge_identity"]
    )

    with pytest.raises(AssertionError):
        assert_unique_multi_edge_identities(graph)


def test_firewall_is_one_canonical_entity_with_read_only_scope_refs() -> None:
    case = load_fixture()
    expected = case["scenario_expectations"]
    graph = case["expected_presentation_graph"]
    matches = [
        item
        for item in graph["entities"]
        if item["id"] == expected["multi_scope_firewall_id"]
    ]
    assert len(matches) == 1
    assert matches[0]["scope_memberships"] == expected["firewall_scope_ids"]
    assert_firewall_scope_refs(case)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_firewall_entity_ref_scope_mutations_fail_closed(mutation: str) -> None:
    case = load_fixture()
    refs = case["scenario_expectations"]["firewall_entity_refs"]
    if mutation == "missing":
        refs.pop()
    else:
        refs.append(
            {
                "id": "entity-ref:fortinet-ha:extra",
                "scope_id": "scope:organization:root",
                "canonical_entity_id": case["scenario_expectations"][
                    "multi_scope_firewall_id"
                ],
                "read_only": True,
            }
        )

    with pytest.raises(AssertionError):
        assert_firewall_scope_refs(case)


def validate_entity_refs(entity_refs: list[dict[str, Any]]) -> None:
    schema = contracts.load_schema("aioe.network-topology.graph/v2")
    validator = Draft202012Validator(schema["$defs"]["entityRef"])
    for item in entity_refs:
        validator.validate(item)
        assert item["read_only"] is True


def test_explicit_access_and_mlag_facts_have_exact_observed_ownership() -> None:
    case = load_fixture()
    expectations = case["scenario_expectations"]["evidence_expectations"]
    for item in expectations:
        assert_evidence_expectation(case, item)


def test_access_switch_fact_is_only_exposed_through_ordered_collapse() -> None:
    case = load_fixture()
    expected = case["scenario_expectations"]
    facts = fact_index(case["expected_facts"])
    graph = case["expected_presentation_graph"]
    relations = graph_index(graph, "relations")
    collapsed_fact = expected["collapsed_fact_id"]
    assert collapsed_fact in facts
    assert collapsed_fact not in graph_index(graph, "entities")
    path = relations[expected["collapsed_relation_id"]]
    assert path["relation_type"] == "collapsed_path"
    assert path["member_fact_ids"] == expected["collapsed_member_fact_ids"]
    assert path["member_relation_ids"] == expected["collapsed_member_relation_ids"]
    assert path["attributes"]["ordered_member_fact_ids"] == path["member_fact_ids"]
    assert_access_fact_has_no_fake_physical_link(graph, collapsed_fact)
    assert_access_chain_integrity(case)


@pytest.mark.parametrize(
    "mutation",
    [
        "reused_port",
        "duplicate_remote",
        "reversed_chain",
        "broken_endpoint",
        "unsupported_projection_type",
        "duplicate_wrapper",
    ],
)
def test_access_chain_mutations_fail_closed(mutation: str) -> None:
    case = load_fixture()
    expected = case["scenario_expectations"]
    access_path = expected["access_path"]
    relations = graph_index(case["expected_presentation_graph"], "relations")
    collapsed = relations[expected["collapsed_relation_id"]]
    match mutation:
        case "reused_port":
            access_path["legs"][1]["interface_fact_id"] = access_path["legs"][0][
                "interface_fact_id"
            ]
        case "duplicate_remote":
            facts = fact_index(case["expected_facts"])
            first = facts[access_path["legs"][0]["adjacency_fact_id"]]
            second = facts[access_path["legs"][1]["adjacency_fact_id"]]
            second["attributes"]["remote_id"] = first["attributes"]["remote_id"]
        case "reversed_chain":
            collapsed["attributes"]["ordered_member_fact_ids"].reverse()
        case "broken_endpoint":
            collapsed["endpoints"][1]["ref_id"] = "device-group:synthetic-mlag-1"
        case "unsupported_projection_type":
            collapsed["relation_type"] = "physical_link"
        case "duplicate_wrapper":
            duplicate = deepcopy(collapsed)
            duplicate["id"] = "relation:duplicate-access-wrapper"
            duplicate["relation_type"] = "aggregated_connectivity"
            duplicate["multi_edge_identity"]["relation_type"] = (
                "aggregated_connectivity"
            )
            case["expected_presentation_graph"]["relations"].append(duplicate)

    with pytest.raises(AssertionError):
        assert_access_chain_integrity(case)


def assert_access_fact_has_no_fake_physical_link(
    graph: dict[str, Any], collapsed_fact: str
) -> None:
    carrying = [
        item for item in graph["relations"] if collapsed_fact in item["member_fact_ids"]
    ]
    assert carrying
    assert {item["relation_type"] for item in carrying} <= {
        "collapsed_path",
        "aggregated_connectivity",
    }
    assert all(item["relation_type"] != "physical_link" for item in carrying)


@pytest.mark.parametrize(
    "counter",
    [
        "key_device_count",
        "network_segment_count",
        "child_scope_count",
        "cross_scope_relation_count",
    ],
)
def test_scope_summary_mutations_fail_closed(counter: str) -> None:
    case = load_fixture()
    case["expected_presentation_graph"]["scopes"][0]["summary"][counter] += 1

    with pytest.raises(AssertionError):
        assert_scope_summaries(case)


def test_key_device_count_includes_critical_and_important() -> None:
    case = load_fixture()
    entity = next(
        item
        for item in case["expected_presentation_graph"]["entities"]
        if item["kind"] == "device" and item["importance"] == "critical"
    )
    entity["importance"] = "important"
    assert_scope_summaries(case)
    entity["importance"] = "normal"

    with pytest.raises(AssertionError):
        assert_scope_summaries(case)


@pytest.mark.parametrize(
    "mutation", ["second_root", "cycle", "wrong_primary", "wrong_owner"]
)
def test_scope_tree_and_lca_mutations_fail_closed(mutation: str) -> None:
    case = load_fixture()
    graph = case["expected_presentation_graph"]
    match mutation:
        case "second_root":
            graph["scopes"][1]["parent_scope_id"] = None
        case "cycle":
            graph["scopes"][1]["parent_scope_id"] = graph["scopes"][2]["id"]
            graph["scopes"][2]["parent_scope_id"] = graph["scopes"][1]["id"]
        case "wrong_primary":
            graph_index(graph, "entities")[
                case["scenario_expectations"]["multi_scope_firewall_id"]
            ]["scope_id"] = "scope:network_context:fortinet-fw-1:corp"
        case "wrong_owner":
            graph_index(graph, "relations")["relation:fortinet-fw-1:trust-boundary"][
                "owner_scope_id"
            ] = "scope:security_zone:fortinet-fw-1:trust"

    with pytest.raises(AssertionError):
        assert_scope_tree_integrity(graph)


@pytest.mark.parametrize(
    "mutation",
    ["missing_unresolved", "extra_unresolved", "wrong_locator", "wrong_value"],
)
def test_expectation_table_mutations_fail_closed(mutation: str) -> None:
    case = load_fixture()
    match mutation:
        case "missing_unresolved":
            case["expected_unresolved_references"].pop()
        case "extra_unresolved":
            extra = deepcopy(case["expected_unresolved_references"][0])
            extra["id"] = "unresolved:extra"
            case["expected_unresolved_references"].append(extra)
        case "wrong_locator":
            provenance = case["expected_facts"]["provenance_catalog"][0]["provenance"]
            provenance["locator"]["pointer"] = "/chassis_id"
        case "wrong_value":
            case["provenance_expectations"][0]["expected_value"] = "wrong-value"

    with pytest.raises(AssertionError):
        assert_complete_expectation_tables(case)
