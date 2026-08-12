from __future__ import annotations

import json
from copy import deepcopy
from difflib import SequenceMatcher
from ipaddress import ip_interface
from pathlib import Path
from typing import Any

import pytest

from services.network_topology import contracts
from services.network_topology.canonicalization import (
    CanonicalizationError,
    canonicalize_json,
    canonicalize_observation_data,
    reject_credential_blocks,
    sha256_uri,
)
from services.network_topology.evidence_locator import resolve_evidence
from tests.network_topology.deletion_assertions import assert_scope_summary_integrity
from tests.network_topology.test_phase0_golden_fixtures import (
    assert_fact_reference_integrity,
    assert_graph_reference_integrity,
    iter_facts,
)

FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "network_topology"
    / "adversarial"
    / "contract-adversarial-v1.json"
)
CHUNK_URI = "aioe.network-topology.llm-chunk/v1"
CANDIDATE_URI = "aioe.network-topology.llm-fact-candidates/v1"
FACTS_URI = "aioe.network-topology.facts/v1"
GRAPH_URI = "aioe.network-topology.graph/v2"
COLLECTION_URI = "aioe.network-topology.collection/v2"
CAPABILITY_URI = "aioe.network-topology.registry/capability-profiles/v1"
OBSERVATION_URI = "aioe.network-topology.registry/observation-kinds/v1"
PARSER_URI = "aioe.network-topology.registry/parser-bundle/v1"
LEGACY_CONFIG_URI = "aioe.network-topology.observation/legacy.running_config/v1"
EXPECTED_OUTCOMES = {
    "prompt_injection": "REJECT_UNTRUSTED_CONTROL_FIELDS",
    "forged_evidence": "REJECT_UNVERIFIED_EVIDENCE",
    "similar_names": "KEEP_IDENTITIES_SEPARATE",
    "same_subnet_fake_links": "CREATE_NO_RELATION",
    "credential_leakage": "REJECT_CREDENTIAL_SHAPED_CONTENT",
    "empty_fact_graph": "REJECT_NONEMPTY_MANIFEST_EMPTY_FACTS",
}


@pytest.fixture(scope="module")
def adversarial() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def iter_strings(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_strings(child)
    elif isinstance(value, str):
        yield value


def contains_marker(value: Any, marker: str) -> bool:
    return any(marker in item for item in iter_strings(value))


def validate_chunk_registry_closure(chunk: dict[str, Any]) -> None:
    contracts.validate_registry_integrity()
    contracts.validate_contract(chunk, CHUNK_URI)
    kind_registry = contracts.load_registry(OBSERVATION_URI)
    schema_by_kind = {
        item["kind"]: item["schema_uri"] for item in kind_registry["kinds"]
    }
    parser_registry = contracts.load_registry(PARSER_URI)
    parsers = {
        item["parser_id"]: set(item["observation_kinds"])
        for item in parser_registry["deterministic_parsers"]
    }
    for observation in chunk["observations"]:
        kind = observation["kind"]
        assert schema_by_kind[kind] == observation["data_schema"]
        parser_id = observation["parser_context"]["parser_profile"]
        assert kind in parsers[parser_id]
        canonical = canonicalize_observation_data(
            observation["content_type"], observation["data"]
        )
        if observation["redacted_sha256"] != sha256_uri(canonical):
            raise CanonicalizationError("chunk observation redacted_sha256 mismatch")
        observation_contract = {
            key: value for key, value in observation.items() if key != "redacted_sha256"
        }
        observation_contract.update(
            observation_status="success",
            observed_at="2026-07-28T00:00:00Z",
            raw_sha256=observation["redacted_sha256"],
            content_sha256=observation["redacted_sha256"],
        )
        contracts.validate_contract(observation_contract, schema_by_kind[kind])


def legacy_config_chunk(content: str) -> dict[str, Any]:
    observation = deepcopy(contracts.load_schema(LEGACY_CONFIG_URI)["examples"][0])
    parser_registry = contracts.load_registry(PARSER_URI)
    parser_id = next(
        item["parser_id"]
        for item in parser_registry["deterministic_parsers"]
        if observation["kind"] in item["observation_kinds"]
    )
    observation["parser_context"]["parser_profile"] = parser_id
    observation["data"]["command_blocks"] = [
        {
            "command": "show synthetic allowlisted configuration",
            "content": content,
            "allowlist_rule_id": "synthetic-allowlist-v1",
            "sensitive_sections_removed": True,
        }
    ]
    excluded = {"observation_status", "observed_at", "raw_sha256", "content_sha256"}
    chunk_observation = {
        key: value for key, value in observation.items() if key not in excluded
    }
    chunk_observation["redacted_sha256"] = sha256_uri(
        canonicalize_json(observation["data"])
    )
    chunk = deepcopy(contracts.load_schema(CHUNK_URI)["examples"][0])
    chunk["observations"] = [chunk_observation]
    return chunk


def evidence_observation(
    chunk: dict[str, Any], provenance: dict[str, Any]
) -> dict[str, Any]:
    assert provenance["resource_id"] == chunk["resource_id"]
    matches = [
        item
        for item in chunk["observations"]
        if item["observation_id"] == provenance["observation_id"]
    ]
    assert len(matches) == 1
    return matches[0]


def verified_evidence(
    chunk: dict[str, Any], provenance: dict[str, Any], expected: Any
) -> Any:
    contracts.validate_contract(provenance, "aioe.network-topology.provenance/v1")
    observation = evidence_observation(chunk, provenance)
    canonical = canonicalize_observation_data(
        observation["content_type"], observation["data"]
    )
    content_hash = sha256_uri(canonical)
    if provenance["redacted_sha256"] != observation["redacted_sha256"]:
        raise CanonicalizationError("provenance redacted_sha256 mismatch")
    if observation["redacted_sha256"] != content_hash:
        raise CanonicalizationError("observation redacted_sha256 mismatch")
    resolved = resolve_evidence(observation["data"], provenance["locator"])
    if resolved != expected:
        raise AssertionError("evidence locator resolved the wrong value")
    return resolved


def evidence_key(resource_id: str, reference: dict[str, Any]) -> tuple[str, str, str]:
    locator = json.dumps(reference["locator"], sort_keys=True, separators=(",", ":"))
    return resource_id, reference["observation_id"], locator


def weak_chunk_index(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for chunk in case["chunks"]:
        validate_chunk_registry_closure(chunk)
        for observation in chunk["observations"]:
            observation_id = observation["observation_id"]
            assert observation_id not in result
            result[observation_id] = chunk
    return result


def expected_evidence_index(case: dict[str, Any]) -> dict[tuple[str, str, str], Any]:
    result = {
        evidence_key(item["resource_id"], item): item["expected_value"]
        for item in case["expected_evidence"]
    }
    assert len(result) == len(case["expected_evidence"])
    return result


def candidate_reference_provenance(
    chunk: dict[str, Any], reference: dict[str, Any]
) -> dict[str, Any]:
    observation = next(
        item
        for item in chunk["observations"]
        if item["observation_id"] == reference["observation_id"]
    )
    return {
        "type": "observed",
        "resource_id": chunk["resource_id"],
        "observation_id": reference["observation_id"],
        "redacted_sha256": observation["redacted_sha256"],
        "locator": reference["locator"],
    }


def assert_candidate_evidence_closure(case: dict[str, Any]) -> None:
    chunks = weak_chunk_index(case)
    expected = expected_evidence_index(case)
    candidate = case["candidate_output"]
    items = [*candidate["devices"], *candidate["unresolved_references"]]
    actual_keys: set[tuple[str, str, str]] = set()
    for item in items:
        for reference in item["evidence_refs"]:
            chunk = chunks[reference["observation_id"]]
            key = evidence_key(chunk["resource_id"], reference)
            assert key in expected, "candidate evidence ref is not frozen"
            provenance = candidate_reference_provenance(chunk, reference)
            verified_evidence(chunk, provenance, expected[key])
            actual_keys.add(key)
    assert actual_keys == set(expected)


def assert_formal_and_graph_evidence_closure(case: dict[str, Any]) -> None:
    chunks = weak_chunk_index(case)
    expected = expected_evidence_index(case)
    facts = case["expected_facts"]
    catalog = {item["id"]: item["provenance"] for item in facts["provenance_catalog"]}
    catalog_keys = {
        evidence_key(item["resource_id"], item) for item in catalog.values()
    }
    assert catalog_keys == set(expected)
    for provenance in catalog.values():
        key = evidence_key(provenance["resource_id"], provenance)
        verified_evidence(
            chunks[provenance["observation_id"]], provenance, expected[key]
        )
    fact_keys = {
        evidence_key(catalog[value]["resource_id"], catalog[value])
        for item in facts["devices"]
        for value in item["provenance_ids"]
    }
    graph_provenances = [
        provenance
        for entity in case["expected_graph"]["entities"]
        for provenance in entity["provenance"]
    ]
    graph_keys = {evidence_key(item["resource_id"], item) for item in graph_provenances}
    assert graph_keys == fact_keys
    for provenance in graph_provenances:
        key = evidence_key(provenance["resource_id"], provenance)
        verified_evidence(
            chunks[provenance["observation_id"]], provenance, expected[key]
        )


def assert_weak_bindings(case: dict[str, Any]) -> None:
    candidates = {
        item["candidate_id"]: item for item in case["candidate_output"]["devices"]
    }
    facts = {item["id"]: item for item in case["expected_facts"]["devices"]}
    entities = {item["id"]: item for item in case["expected_graph"]["entities"]}
    chunks = weak_chunk_index(case)
    resource_ids = {chunk["resource_id"] for chunk in case["chunks"]}
    bindings = case["expected_bindings"]
    assert len(candidates) == len(facts) == len(entities) == len(bindings) == 2
    assert {item["resource_id"] for item in bindings} == resource_ids
    for binding in bindings:
        candidate = candidates[binding["candidate_id"]]
        fact = facts[binding["fact_id"]]
        entity = entities[binding["entity_id"]]
        assert fact["source_candidate_ids"] == [candidate["candidate_id"]]
        assert fact["resource_ids"] == [binding["resource_id"]]
        assert candidate["evidence_refs"] == binding["candidate_evidence_refs"]
        assert fact["provenance_ids"] == binding["fact_provenance_ids"]
        actual_graph_refs = {
            evidence_key(item["resource_id"], item) for item in entity["provenance"]
        }
        expected_graph_refs = {
            evidence_key(item["resource_id"], item)
            for item in binding["graph_provenance_refs"]
        }
        assert actual_graph_refs == expected_graph_refs
        owners = {
            chunks[item["observation_id"]]["resource_id"]
            for item in candidate["evidence_refs"]
        }
        assert owners == {binding["resource_id"]}


def assert_unresolved_records_frozen(case: dict[str, Any]) -> None:
    assert (
        case["candidate_output"]["unresolved_references"]
        == case["expected_candidate_unresolved_references"]
    )
    assert (
        case["expected_facts"]["unresolved_references"]
        == case["expected_formal_unresolved_references"]
    )


def assert_nonempty_manifest_has_facts(
    manifest: dict[str, Any], facts: dict[str, Any]
) -> None:
    fact_items = list(iter_facts(facts))
    if manifest["expected_resources"] and not fact_items:
        raise contracts.ContractError(
            "non-empty Manifest cannot publish an empty FactGraph"
        )
    manifest_ids = {item["resource_id"] for item in manifest["expected_resources"]}
    fact_resource_ids = {
        resource_id for item in fact_items for resource_id in item["resource_ids"]
    }
    if manifest_ids != fact_resource_ids:
        raise contracts.ContractError(
            "FactGraph resource binding does not match Manifest"
        )


def test_fixture_is_compact_synthetic_and_freezes_all_outcomes(adversarial) -> None:
    assert adversarial["synthetic_redacted"] is True
    assert len(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()) <= 800
    assert adversarial["expected_outcomes"] == EXPECTED_OUTCOMES


def test_prompt_injection_is_data_only_and_control_fields_fail_schema(
    adversarial,
) -> None:
    case = adversarial["prompt_injection"]
    marker = case["marker"]
    validate_chunk_registry_closure(case["chunk"])
    validate_chunk_registry_closure(case["control_chunk"])
    contracts.validate_contract(case["accepted_candidate"], CANDIDATE_URI)
    contracts.validate_contract(case["expected_facts"], FACTS_URI)
    contracts.validate_contract(case["expected_graph"], GRAPH_URI)
    assert contains_marker(case["chunk"]["observations"][0]["data"], marker)
    parser_surfaces = {k: v for k, v in case["chunk"].items() if k != "observations"}
    observation_surfaces = {
        key: value
        for key, value in case["chunk"]["observations"][0].items()
        if key != "data"
    }
    assert not contains_marker(parser_surfaces, marker)
    assert not contains_marker(observation_surfaces, marker)
    assert not contains_marker(case["accepted_candidate"], marker)
    assert not contains_marker(case["expected_facts"], marker)
    assert not contains_marker(case["expected_graph"], marker)
    with pytest.raises(contracts.ContractError):
        contracts.validate_contract(case["attempted_control_output"], CANDIDATE_URI)
    assert (
        verified_evidence(
            case["control_chunk"],
            case["control_provenance"],
            case["control_expected_value"],
        )
        == "eth0"
    )


def test_prompt_chunk_observation_hash_drift_fails_closed(adversarial) -> None:
    chunk = deepcopy(adversarial["prompt_injection"]["chunk"])
    chunk["observations"][0]["redacted_sha256"] = "sha256:" + "0" * 64

    with pytest.raises(CanonicalizationError, match="chunk observation"):
        validate_chunk_registry_closure(chunk)


def test_forged_locator_and_hash_fail_against_real_evidence_binding(
    adversarial,
) -> None:
    case = adversarial["forged_evidence"]
    validate_chunk_registry_closure(case["chunk"])
    expected = case["expected_value"]
    assert (
        verified_evidence(case["chunk"], case["valid_provenance"], expected) == expected
    )
    with pytest.raises(AssertionError, match="wrong value"):
        verified_evidence(case["chunk"], case["wrong_but_resolvable_locator"], expected)
    with pytest.raises(CanonicalizationError, match="provenance redacted_sha256"):
        verified_evidence(case["chunk"], case["forged_hash_provenance"], expected)


def test_similar_names_and_shared_subnet_do_not_merge_or_link(adversarial) -> None:
    case = adversarial["weak_similarity"]
    chunks = case["chunks"]
    for chunk in chunks:
        validate_chunk_registry_closure(chunk)
    contracts.validate_contract(case["candidate_output"], CANDIDATE_URI)
    contracts.validate_contract(case["expected_facts"], FACTS_URI)
    contracts.validate_contract(case["expected_graph"], GRAPH_URI)
    identity_data = [chunk["observations"][0]["data"] for chunk in chunks]
    interfaces = [chunk["observations"][1]["data"]["interfaces"][0] for chunk in chunks]
    names = [item["hostname"] for item in identity_data]
    networks = [
        ip_interface(item["addresses"][0]["prefix"]).network for item in interfaces
    ]
    assert SequenceMatcher(None, *names).ratio() > 0.9
    assert networks[0] == networks[1]
    assert all(item["name"] == "eth0" for item in interfaces)
    facts = case["expected_facts"]
    graph = case["expected_graph"]
    bound_ids = [item["fact_id"] for item in case["expected_bindings"]]
    assert case["expected_merge_groups"] == [[item] for item in bound_ids]
    assert facts["adjacencies"] == []
    assert [item["id"] for item in graph["relations"]] == case["expected_relation_ids"]
    assert_weak_bindings(case)
    assert_fact_reference_integrity(facts)
    assert_graph_reference_integrity(graph, facts)
    assert_scope_summary_integrity(graph)


def test_weak_signal_unresolved_and_all_evidence_are_frozen(adversarial) -> None:
    case = adversarial["weak_similarity"]
    assert_unresolved_records_frozen(case)
    assert_candidate_evidence_closure(case)
    assert_formal_and_graph_evidence_closure(case)


def test_valid_but_wrong_candidate_locator_fails_closed(adversarial) -> None:
    case = deepcopy(adversarial["weak_similarity"])
    reference = case["candidate_output"]["devices"][0]["evidence_refs"][0]
    reference["locator"]["pointer"] = "/serial"
    identity = case["chunks"][0]["observations"][0]["data"]
    assert resolve_evidence(identity, reference["locator"]) == "SYNTH-A-0001"

    with pytest.raises(AssertionError):
        assert_weak_bindings(case)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_candidate_match_drift_fails_closed(adversarial, mutation) -> None:
    case = deepcopy(adversarial["weak_similarity"])
    matches = case["candidate_output"]["unresolved_references"][0]["candidate_matches"]
    if mutation == "missing":
        matches.pop()
    else:
        matches.append("candidate:unexpected")

    with pytest.raises(AssertionError):
        assert_unresolved_records_frozen(case)


@pytest.mark.parametrize("mutation", ["missing_fact", "swapped_graph"])
def test_weak_provenance_drift_fails_closed(adversarial, mutation) -> None:
    case = deepcopy(adversarial["weak_similarity"])
    if mutation == "missing_fact":
        case["expected_facts"]["devices"][0]["provenance_ids"].pop()
    else:
        entities = case["expected_graph"]["entities"]
        entities[0]["provenance"], entities[1]["provenance"] = (
            entities[1]["provenance"],
            entities[0]["provenance"],
        )

    with pytest.raises(AssertionError):
        assert_weak_bindings(case)


def test_weak_scope_summary_drift_fails_closed(adversarial) -> None:
    graph = deepcopy(adversarial["weak_similarity"]["expected_graph"])
    graph["scopes"][0]["summary"]["key_device_count"] = 1

    with pytest.raises(AssertionError):
        assert_scope_summary_integrity(graph)


def test_synthetic_credential_shaped_content_is_rejected(adversarial) -> None:
    case = adversarial["credential_leakage"]
    with pytest.raises(CanonicalizationError, match=case["expected_error_contains"]):
        canonicalize_observation_data(case["content_type"], case["data"])
    with pytest.raises(CanonicalizationError, match=case["expected_error_contains"]):
        reject_credential_blocks({"observation": case["data"]})
    with pytest.raises(CanonicalizationError, match=case["expected_error_contains"]):
        validate_chunk_registry_closure(legacy_config_chunk(case["data"]))
    validate_chunk_registry_closure(legacy_config_chunk(case["control_data"]))
    assert canonicalize_observation_data(case["content_type"], case["control_data"])


def test_nonempty_manifest_with_empty_formal_facts_fails(adversarial) -> None:
    case = adversarial["empty_fact_graph"]
    facts = case["formal_facts"]
    contracts.validate_contract(facts, FACTS_URI)
    collection = contracts.load_schema(COLLECTION_URI)["examples"][0]
    collection["target_manifest"] = deepcopy(case["manifest"])
    contracts.validate_contract(collection, COLLECTION_URI)
    registry = contracts.load_registry(CAPABILITY_URI)
    profile = next(
        item
        for item in registry["profiles"]
        if item["id"] == case["manifest"]["expected_resources"][0]["capability_profile"]
    )
    resource = case["manifest"]["expected_resources"][0]
    assert resource["required_kinds"] == profile["required_kinds"]
    assert resource["optional_kinds"] == profile["optional_kinds"]
    with pytest.raises(contracts.ContractError, match=case["expected_error_contains"]):
        assert_nonempty_manifest_has_facts(case["manifest"], facts)


def test_valid_nonempty_fact_control_passes_acceptance_gate(adversarial) -> None:
    case = adversarial["empty_fact_graph"]
    facts = deepcopy(adversarial["weak_similarity"]["expected_facts"])
    manifest_resource_id = case["manifest"]["expected_resources"][0]["resource_id"]
    for item in iter_facts(facts):
        item["resource_ids"] = [manifest_resource_id]
    contracts.validate_contract(facts, FACTS_URI)
    assert_nonempty_manifest_has_facts(case["manifest"], facts)


@pytest.mark.parametrize("mismatch", ["missing_manifest_resource", "escaping_fact"])
def test_manifest_fact_resource_binding_mismatch_fails(adversarial, mismatch) -> None:
    case = adversarial["empty_fact_graph"]
    facts = deepcopy(adversarial["weak_similarity"]["expected_facts"])
    manifest = deepcopy(case["manifest"])
    manifest_id = manifest["expected_resources"][0]["resource_id"]
    for item in iter_facts(facts):
        item["resource_ids"] = [manifest_id]
    if mismatch == "missing_manifest_resource":
        manifest["expected_resources"][0]["resource_id"] = "resource:unrepresented"
    else:
        facts["devices"][0]["resource_ids"].append("resource:escaping")
    with pytest.raises(contracts.ContractError, match="resource binding"):
        assert_nonempty_manifest_has_facts(manifest, facts)
