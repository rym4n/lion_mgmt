from __future__ import annotations

from typing import Any

from services.network_topology import contracts
from services.network_topology.canonicalization import (
    canonicalize_and_hash_snapshot,
    canonicalize_json,
    sha256_uri,
    validate_observation_hashes,
)
from tests.network_topology.scope_assertions import recompute_scope_summary
from tests.network_topology.test_phase0_golden_fixtures import (
    assert_case_output_binding,
    assert_derived_member_integrity,
    assert_fact_reference_integrity,
    assert_graph_reference_integrity,
    assert_manifest_profile_closure,
    assert_provenance_integrity,
    assert_provenance_resolves,
    authoritative_index,
    fact_index,
    iter_provenances,
    iter_observations,
    observation_index,
)


def assert_legal_full_snapshot_deletion(case: dict[str, Any]) -> None:
    assert case["fixture_version"] == "aioe.network-topology.deletion-golden/v1"
    assert case["synthetic_redacted"] is True
    assert case["publication_expectation"] == "contract_only"
    for phase in ("baseline", "next"):
        assert_snapshot_contract(case[phase])
    assert_snapshot_lineage(case)
    assert_revision_progression(case)
    assert_exact_id_deltas(case)


def assert_snapshot_contract(snapshot: dict[str, Any]) -> None:
    collection = snapshot["collection"]
    facts = snapshot["expected_facts"]
    graph = snapshot["expected_presentation_graph"]
    contracts.validate_contract(collection, "aioe.network-topology.collection/v2")
    contracts.validate_contract(facts, "aioe.network-topology.facts/v1")
    contracts.validate_contract(graph, "aioe.network-topology.graph/v2")
    for observation in iter_observations(collection):
        validate_observation_hashes(observation)
    assert_frozen_hashes(snapshot)
    assert_manifest_profile_closure(collection)
    assert_case_output_binding(snapshot)
    assert_fact_reference_integrity(facts)
    assert_graph_reference_integrity(graph, facts)
    assert_scope_summary_integrity(graph)
    assert_derived_member_integrity(snapshot)
    assert_provenance_integrity(snapshot)
    assert_snapshot_expectations(snapshot)


def assert_frozen_hashes(snapshot: dict[str, Any]) -> None:
    hashes = snapshot["canonical_hashes"]
    assert (
        canonicalize_and_hash_snapshot(snapshot["collection"]).payload_hash
        == hashes["snapshot"]
    )
    assert sha256_uri(canonicalize_json(snapshot["expected_facts"])) == hashes["facts"]
    graph_bytes = canonicalize_json(snapshot["expected_presentation_graph"])
    assert sha256_uri(graph_bytes) == hashes["graph"]


def assert_snapshot_expectations(snapshot: dict[str, Any]) -> None:
    facts = snapshot["expected_facts"]
    entries = snapshot["expected_provenance_values"]
    expected = {item["provenance_id"]: item["expected_value"] for item in entries}
    catalog = {item["id"]: item["provenance"] for item in facts["provenance_catalog"]}
    assert len(expected) == len(entries)
    assert set(catalog) == set(expected)
    observations = observation_index(snapshot["collection"])
    authoritative = authoritative_index(snapshot["collection"])
    for provenance_id, provenance in catalog.items():
        value = assert_provenance_resolves(provenance, observations, authoritative)
        assert value == expected[provenance_id]
    assert facts["unresolved_references"] == snapshot["expected_unresolved_references"]
    assert_graph_provenance_matches_catalog(snapshot, list(catalog.values()))


def assert_graph_provenance_matches_catalog(
    snapshot: dict[str, Any], catalog: list[dict[str, Any]]
) -> None:
    graph = snapshot["expected_presentation_graph"]
    for provenance in iter_provenances(graph):
        if provenance["type"] != "derived":
            assert provenance in catalog


def assert_scope_summary_integrity(graph: dict[str, Any]) -> None:
    for scope in graph["scopes"]:
        assert scope["summary"] == recompute_scope_summary(graph, scope["id"])


def assert_revision_progression(case: dict[str, Any]) -> None:
    expected = case["deletion_expectations"]["revisions"]
    baseline = manifest_revisions(case["baseline"])
    next_value = manifest_revisions(case["next"])
    assert baseline == expected["baseline"]
    assert next_value == expected["next"]
    assert next_value["collection_sequence"] == baseline["collection_sequence"] + 1
    assert next_value["base_graph_revision"] == baseline["base_graph_revision"] + 1
    assert next_value["base_publication_revision"] == (
        baseline["base_publication_revision"] + 1
    )


def assert_snapshot_lineage(case: dict[str, Any]) -> None:
    baseline = case["baseline"]["collection"]
    next_value = case["next"]["collection"]
    baseline_resources = baseline["completeness"]["expected_resource_ids"]
    next_resources = next_value["completeness"]["expected_resource_ids"]
    assert baseline_resources == next_resources
    assert (
        baseline["collector"]["playbook_id"] == next_value["collector"]["playbook_id"]
    )
    assert baseline["collector"]["run_id"] != next_value["collector"]["run_id"]
    assert baseline["snapshot_id"] != next_value["snapshot_id"]
    assert (
        baseline["topology_profile_revision"]
        == (next_value["topology_profile_revision"])
    )


def manifest_revisions(snapshot: dict[str, Any]) -> dict[str, int]:
    manifest = snapshot["collection"]["target_manifest"]
    return {
        field: manifest[field]
        for field in (
            "collection_sequence",
            "base_graph_revision",
            "base_publication_revision",
        )
    }


def assert_exact_id_deltas(case: dict[str, Any]) -> None:
    expected = case["deletion_expectations"]
    for surface, actual in topology_id_sets(case["baseline"]).items():
        next_ids = topology_id_sets(case["next"])[surface]
        assert sorted(actual - next_ids) == expected[surface]["removed"]
        assert sorted(next_ids - actual) == expected[surface]["added"]
        assert sorted(actual & next_ids) == expected[surface]["retained"]
        assert sorted(actual) == expected[surface]["baseline"]
        assert sorted(next_ids) == expected[surface]["next"]


def topology_id_sets(snapshot: dict[str, Any]) -> dict[str, set[str]]:
    facts = set(fact_index(snapshot["expected_facts"]))
    graph = snapshot["expected_presentation_graph"]
    return {
        "facts": facts,
        "graph_entities": {item["id"] for item in graph["entities"]},
        "graph_relations": {item["id"] for item in graph["relations"]},
        "provenance": {
            item["id"] for item in snapshot["expected_facts"]["provenance_catalog"]
        },
        "unresolved": {
            item["id"] for item in snapshot["expected_facts"]["unresolved_references"]
        },
    }
