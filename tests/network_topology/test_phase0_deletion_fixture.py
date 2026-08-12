from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from services.network_topology.canonicalization import (
    canonicalize_and_hash_snapshot,
    canonicalize_json,
    sha256_uri,
)
from tests.network_topology.deletion_assertions import (
    assert_legal_full_snapshot_deletion,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "network_topology"
    / "deletion"
    / "full-deletion-v1.json"
)


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def expect_rejected(case: dict[str, Any]) -> None:
    with pytest.raises((AssertionError, KeyError, ValueError)):
        assert_legal_full_snapshot_deletion(case)


def test_full_snapshot_deletion_expectation_is_closed_and_exact() -> None:
    case = load_fixture()

    assert_legal_full_snapshot_deletion(case)
    expected = case["deletion_expectations"]
    assert len(expected["facts"]["removed"]) >= 4
    assert len(expected["graph_entities"]["removed"]) == 2
    assert len(expected["graph_relations"]["removed"]) == 2
    assert expected["facts"]["added"] == []


@pytest.mark.parametrize("reporting", ["underreported", "overreported"])
def test_misreported_deletion_set_fails_closed(reporting: str) -> None:
    case = load_fixture()
    removed = case["deletion_expectations"]["facts"]["removed"]
    if reporting == "underreported":
        removed.pop()
    else:
        removed.append("fact:invented")

    expect_rejected(case)


@pytest.mark.parametrize("delta", ["added", "retained"])
def test_misreported_non_deletion_delta_fails_closed(delta: str) -> None:
    case = load_fixture()
    case["deletion_expectations"]["facts"][delta].append("fact:invented")

    expect_rejected(case)


def test_heuristically_retained_old_fact_fails_closed() -> None:
    case = load_fixture()
    removed_id = case["deletion_expectations"]["facts"]["removed"][0]
    baseline = case["baseline"]["expected_facts"]
    stale = next(
        item
        for values in baseline.values()
        if isinstance(values, list)
        for item in values
        if isinstance(item, dict) and item.get("id") == removed_id
    )
    case["next"]["expected_facts"]["interfaces"].append(deepcopy(stale))
    refresh_hashes(case["next"])

    expect_rejected(case)


def test_unknown_capability_profile_fails_closed() -> None:
    case = load_fixture()
    manifest = case["next"]["collection"]["target_manifest"]["expected_resources"][0]
    manifest["capability_profile"] = "aioe.synthetic.unknown/v1"
    refresh_hashes(case["next"])

    expect_rejected(case)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_incomplete_provenance_expectation_table_fails_closed(mutation: str) -> None:
    case = load_fixture()
    expected = case["next"]["expected_provenance_values"]
    if mutation == "missing":
        expected.pop()
    else:
        expected.append({"provenance_id": "provenance:extra", "expected_value": None})

    expect_rejected(case)


def test_valid_but_wrong_provenance_locator_fails_closed() -> None:
    case = load_fixture()
    catalog = case["next"]["expected_facts"]["provenance_catalog"]
    identity = next(item for item in catalog if item["id"] == "provenance:identity")
    identity["provenance"]["locator"]["pointer"] = "/serial"
    refresh_hashes(case["next"])

    expect_rejected(case)


def test_unresolved_reference_expectation_drift_fails_closed() -> None:
    case = load_fixture()
    case["next"]["expected_unresolved_references"].append(
        {
            "id": "unresolved:invented",
            "selector": {"name": "synthetic"},
            "reason_code": "INVENTED_EXPECTATION",
            "candidate_matches": [],
            "provenance_ids": ["provenance:identity"],
        }
    )

    expect_rejected(case)


@pytest.mark.parametrize("field", ["mode", "asserted", "failed_resource_ids"])
def test_non_authoritative_next_collection_cannot_publish_deletions(field: str) -> None:
    case = load_fixture()
    completeness = case["next"]["collection"]["completeness"]
    mutations = {"mode": "incremental", "asserted": False, "failed_resource_ids": ["r"]}
    completeness[field] = mutations[field]

    expect_rejected(case)


def test_failed_required_observation_cannot_publish_deletions() -> None:
    case = load_fixture()
    collection = case["next"]["collection"]
    resource = collection["sources"][0]["resources"][0]
    observation = resource["result"]["observations"][0]
    observation.update(
        observation_status="failed",
        data=None,
        reason_code="SYNTHETIC_FAILURE",
        reason_summary="Synthetic negative mutation",
    )
    coverage = collection["completeness"]["observation_coverage"][0]
    coverage["kind_status"][observation["kind"]] = "failed"
    empty_hash = sha256_uri(canonicalize_json(None))
    observation["raw_sha256"] = empty_hash
    observation["content_sha256"] = empty_hash
    refresh_hashes(case["next"])

    expect_rejected(case)


@pytest.mark.parametrize("target", ["endpoint", "member", "typed_fact", "provenance"])
def test_dangling_next_output_fails_closed(target: str) -> None:
    case = load_fixture()
    mutate_dangling_reference(case["next"], target)
    refresh_hashes(case["next"])

    expect_rejected(case)


def mutate_dangling_reference(snapshot: dict[str, Any], target: str) -> None:
    facts = snapshot["expected_facts"]
    graph = snapshot["expected_presentation_graph"]
    if target == "endpoint":
        graph["relations"][0]["endpoints"][0]["ref_id"] = "entity:missing"
    elif target == "member":
        graph["relations"][0]["member_fact_ids"] = ["fact:missing"]
    elif target == "typed_fact":
        facts["interfaces"][0]["attributes"]["device_id"] = "device:missing"
    else:
        facts["devices"][0]["provenance_ids"] = ["provenance:missing"]


def refresh_hashes(snapshot: dict[str, Any]) -> None:
    snapshot["canonical_hashes"] = {
        "snapshot": canonicalize_and_hash_snapshot(snapshot["collection"]).payload_hash,
        "facts": sha256_uri(canonicalize_json(snapshot["expected_facts"])),
        "graph": sha256_uri(canonicalize_json(snapshot["expected_presentation_graph"])),
    }
