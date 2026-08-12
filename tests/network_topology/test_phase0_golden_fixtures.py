from __future__ import annotations

import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from services.network_topology import contracts
from services.network_topology.canonicalization import (
    canonicalize_and_hash_snapshot,
    canonicalize_json,
    sha256_uri,
    validate_observation_hashes,
)
from services.network_topology.evidence_locator import resolve_evidence
from tests.network_topology.scope_assertions import assert_scope_tree_integrity

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "network_topology"
GOLDEN_FILES = tuple(sorted(FIXTURE_ROOT.glob("*/full-golden-v1.json")))
EXPECTED_CASES = {
    "adc_lb",
    "cisco",
    "fortinet",
    "h3c",
    "huawei",
}
CAPABILITY_URI = "aioe.network-topology.registry/capability-profiles/v1"
FACT_COLLECTIONS = (
    "devices",
    "device_groups",
    "network_contexts",
    "interfaces",
    "segments",
    "adjacencies",
    "routes",
    "routing_relationships",
    "tunnels",
    "memberships",
    "gateways",
    "security_boundaries",
    "wan_connections",
)
FACT_REFERENCE_FIELDS = {
    "device_group": ("member_ids",),
    "network_context": ("owner_id", "parent_context_id"),
    "vrf": ("owner_id", "interface_ids"),
    "vlan": ("owner_id", "interface_ids"),
    "zone": ("owner_id", "interface_ids"),
    "interface": (
        "device_id",
        "context_id",
        "parent_interface_id",
        "lag_id",
        "vrf_id",
        "zone_id",
    ),
    "network": ("device_id", "context_id", "vrf_id", "gateway_ids"),
    "neighbor": ("local_id", "remote_id", "context_id"),
    "route": ("vrf_id", "next_hop_id", "interface_id"),
    "routing_relationship": (
        "local_id",
        "remote_id",
        "vrf_id",
        "interface_id",
    ),
    "tunnel": ("context_id", "interface_id"),
    "membership": ("member_id", "container_id"),
    "gateway": ("gateway_id", "segment_id"),
    "security": ("device_id", "interface_id", "zone_id"),
    "wan": ("local_id", "remote_id"),
}
EXPECTED_FACT_IDS = {
    "huawei": {
        "interface:huawei-core-1:ge0-0-1",
        "interface:huawei-core-1:eth-trunk1",
        "vlan:huawei-core-1:100",
        "vrf:huawei-core-1:blue",
        "neighbor:huawei-core-1:ge0-0-1:remote-99",
        "membership:huawei-core-1:ge0-0-1:lag1",
    },
    "h3c": {
        "interface:h3c-dist-1:xge1-0-1",
        "vlan:h3c-dist-1:210",
        "vrf:h3c-dist-1:default",
        "network:h3c-dist-1:198.51.100.16-28",
        "neighbor:h3c-dist-1:xge1-0-1:remote-98",
    },
    "cisco": {
        "interface:cisco-edge-1:gi1-0-1",
        "vrf:cisco-edge-1:default",
        "route:cisco-edge-1:default",
    },
    "fortinet": {
        "interface:fortinet-fw-1:port1",
        "vrf:fortinet-fw-1:corp",
        "zone:fortinet-fw-1:trust",
        "network:fortinet-fw-1:198.51.100.128-25",
        "route:fortinet-fw-1:default",
        "tunnel:fortinet-fw-1:syn-vpn-01",
        "security:fortinet-fw-1:trust:port1",
    },
    "adc_lb": {
        "device:adc-lb-1",
        "device:adc-lb-2",
        "device-group:adc-cluster-1",
        "interface:adc-lb-1:data1",
        "vrf:adc-lb-1:default",
        "network:adc-lb-1:198.51.100.192-26",
        "gateway:adc-lb-1:vip-https",
        "route:adc-lb-1:default",
        "network:synthetic-internet",
        "network:synthetic-vpc",
        "wan:synthetic-circuit-001",
    },
}
EXPECTED_GRAPH_IDS = {
    "huawei": {
        "scope:network_context:huawei-core-1:blue",
        "scope:network_context:huawei-core-1:vlan-100",
        "device:huawei-core-1",
    },
    "h3c": {
        "scope:network_context:h3c-dist-1:default",
        "scope:network_context:h3c-dist-1:vlan-210",
        "network:h3c-dist-1:198.51.100.16-28",
    },
    "cisco": {
        "scope:network_context:cisco-edge-1:default",
        "device:cisco-edge-1",
    },
    "fortinet": {
        "scope:network_context:fortinet-fw-1:corp",
        "scope:security_zone:fortinet-fw-1:trust",
        "network:fortinet-fw-1:198.51.100.128-25",
        "relation:fortinet-fw-1:trust-boundary",
    },
    "adc_lb": {
        "scope:network_context:adc-lb-1:default",
        "device-group:adc-cluster-1",
        "network:adc-lb-1:198.51.100.192-26",
        "network:synthetic-internet",
        "network:synthetic-vpc",
        "relation:adc-lb-1:vip-https",
        "relation:synthetic-circuit-001",
    },
}


def load_case(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_observations(collection: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for source in collection["sources"]:
        for resource in source["resources"]:
            yield from resource["result"]["observations"]


def iter_resource_observations(
    collection: dict[str, Any],
) -> Iterator[tuple[str, dict[str, Any]]]:
    for source in collection["sources"]:
        for resource in source["resources"]:
            for observation in resource["result"]["observations"]:
                yield resource["resource_id"], observation


def observation_index(
    collection: dict[str, Any],
) -> dict[str, tuple[str, dict[str, Any]]]:
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for resource_id, observation in iter_resource_observations(collection):
        observation_id = observation["observation_id"]
        assert observation_id not in result, f"duplicate observation {observation_id}"
        result[observation_id] = (resource_id, observation)
    return result


def authoritative_index(collection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    context = collection["authoritative_context"]
    records: dict[str, dict[str, Any]] = {}
    for family in ("subnets", "external_networks", "circuits", "cloud_networks"):
        records.update({record["record_id"]: record for record in context[family]})
    return records


def iter_provenances(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if value.get("type") in {"observed", "authoritative", "derived"}:
            yield value
            return
        for child in value.values():
            yield from iter_provenances(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_provenances(child)


def assert_authoritative_locator(
    provenance: dict[str, Any], records: dict[str, dict[str, Any]]
) -> Any:
    assert provenance["source_record_id"] in records
    record = records[provenance["source_record_id"]]
    source = record["provenance"]
    for field in ("source_system", "source_record_id", "source_revision", "as_of"):
        assert provenance[field] == source[field]
    assert provenance["field_locator"] == source["field_locator"]
    locator = {"type": "json_pointer", "pointer": provenance["field_locator"]}
    return resolve_evidence(record, locator)


def assert_provenance_resolves(
    provenance: dict[str, Any],
    observations: dict[str, tuple[str, dict[str, Any]]],
    authoritative: dict[str, dict[str, Any]],
) -> Any:
    if provenance["type"] == "derived":
        return None
    if provenance["type"] == "authoritative":
        return assert_authoritative_locator(provenance, authoritative)
    assert provenance["observation_id"] in observations
    resource_id, observation = observations[provenance["observation_id"]]
    assert provenance["resource_id"] == resource_id
    assert provenance["redacted_sha256"] == observation["content_sha256"]
    return resolve_evidence(observation["data"], provenance["locator"])


def observed_kinds_by_resource(collection: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for source in collection["sources"]:
        for resource in source["resources"]:
            result[resource["resource_id"]] = {
                item["kind"] for item in resource["result"]["observations"]
            }
    return result


def unique_resource_index(
    items: Iterator[dict[str, Any]], label: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        resource_id = item["resource_id"]
        assert resource_id not in result, f"duplicate {label} resource {resource_id}"
        result[resource_id] = item
    return result


def resource_closure_indexes(
    collection: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    manifests = unique_resource_index(
        iter(collection["target_manifest"]["expected_resources"]), "Manifest"
    )
    bindings = unique_resource_index(
        (
            resource
            for source in collection["sources"]
            for resource in source["resources"]
        ),
        "source binding",
    )
    coverage = unique_resource_index(
        iter(collection["completeness"]["observation_coverage"]), "coverage"
    )
    assert set(manifests) == set(bindings) == set(coverage)
    return manifests, bindings, coverage


def assert_manifest_profile_closure(collection: dict[str, Any]) -> None:
    registry = contracts.load_registry(CAPABILITY_URI)
    profiles = {item["id"]: item for item in registry["profiles"]}
    manifests, bindings, coverage = resource_closure_indexes(collection)
    for manifest in manifests.values():
        profile = profiles[manifest["capability_profile"]]
        assert manifest["required_kinds"] == profile["required_kinds"]
        assert manifest["optional_kinds"] == profile["optional_kinds"]
        assert_resource_outcome_closure(manifest, bindings, coverage)


def assert_resource_outcome_closure(
    manifest: dict[str, Any],
    bindings: dict[str, dict[str, Any]],
    coverage: dict[str, dict[str, Any]],
) -> None:
    resource_id = manifest["resource_id"]
    expected = manifest["required_kinds"] + manifest["optional_kinds"]
    observations = bindings[resource_id]["result"]["observations"]
    by_kind = {item["kind"]: item for item in observations}
    assert len(by_kind) == len(observations)
    assert list(by_kind) == expected
    statuses = {kind: item["observation_status"] for kind, item in by_kind.items()}
    assert all(statuses[kind] == "success" for kind in manifest["required_kinds"])
    assert set(statuses.values()) <= {"success", "not_supported", "not_applicable"}
    for observation in observations:
        if observation["observation_status"] != "success":
            assert observation["data"] is None
            assert observation["reason_code"] and observation["reason_summary"]
    assert coverage[resource_id]["collected_kinds"] == expected
    assert coverage[resource_id]["kind_status"] == statuses


@pytest.mark.parametrize("case_name", ["huawei", "h3c", "cisco", "fortinet", "adc_lb"])
def test_registered_manifest_profile_and_outcome_closure(case_name: str) -> None:
    case = load_case(FIXTURE_ROOT / case_name / "full-golden-v1.json")

    assert_manifest_profile_closure(case["collection"])


def iter_facts(facts: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for collection in FACT_COLLECTIONS:
        yield from facts[collection]


def fact_index(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in iter_facts(facts):
        assert item["id"] not in result, f"duplicate Fact {item['id']}"
        result[item["id"]] = item
    return result


def iter_typed_fact_references(item: dict[str, Any]) -> Iterator[str]:
    attributes = item["attributes"]
    for field in FACT_REFERENCE_FIELDS.get(item["fact_family"], ()):
        value = attributes.get(field)
        if isinstance(value, list):
            yield from value
        elif value is not None:
            yield value


def assert_fact_reference_integrity(facts: dict[str, Any]) -> None:
    indexed = fact_index(facts)
    unresolved = {item["id"]: item for item in facts["unresolved_references"]}
    catalog_ids = {item["id"] for item in facts["provenance_catalog"]}
    assert len(unresolved) == len(facts["unresolved_references"])
    assert len(catalog_ids) == len(facts["provenance_catalog"])
    targets = set(indexed) | set(unresolved)
    for item in indexed.values():
        assert set(item["provenance_ids"]) <= catalog_ids
        assert set(iter_typed_fact_references(item)) <= targets
    for item in unresolved.values():
        assert set(item["provenance_ids"]) <= catalog_ids


def assert_graph_reference_integrity(
    graph: dict[str, Any], facts: dict[str, Any]
) -> None:
    fact_ids = set(fact_index(facts))
    scope_ids = {item["id"] for item in graph["scopes"]}
    entity_ids = {item["id"] for item in graph["entities"]}
    relation_ids = {item["id"] for item in graph["relations"]}
    assert len(scope_ids) == len(graph["scopes"])
    assert len(entity_ids) == len(graph["entities"])
    assert len(relation_ids) == len(graph["relations"])
    assert graph["root_scope_id"] in scope_ids
    assert entity_ids <= fact_ids
    for scope_item in graph["scopes"]:
        assert (
            scope_item["parent_scope_id"] is None
            or scope_item["parent_scope_id"] in scope_ids
        )
    for entity in graph["entities"]:
        assert {entity["scope_id"], *entity["scope_memberships"]} <= scope_ids
    assert_relation_references(graph, scope_ids, entity_ids, fact_ids, relation_ids)
    assert_scope_tree_integrity(graph)


def assert_relation_references(
    graph: dict[str, Any],
    scope_ids: set[str],
    entity_ids: set[str],
    fact_ids: set[str],
    relation_ids: set[str],
) -> None:
    for relation_item in graph["relations"]:
        assert relation_item["owner_scope_id"] in scope_ids
        assert set(relation_item["member_fact_ids"]) <= fact_ids
        assert set(relation_item["member_relation_ids"]) <= relation_ids
        for endpoint in relation_item["endpoints"]:
            allowed = entity_ids if endpoint["ref_type"] == "entity" else scope_ids
            assert endpoint["ref_id"] in allowed


def assert_derived_member_integrity(case: dict[str, Any]) -> None:
    facts = case["expected_facts"]
    graph = case["expected_presentation_graph"]
    approved = {facts["snapshot_id"], *fact_index(facts)}
    approved.update(item["id"] for item in graph["scopes"])
    approved.update(item["id"] for item in graph["entities"])
    approved.update(item["id"] for item in graph["relations"])
    approved.update(authoritative_index(case["collection"]))
    for provenance in iter_provenances([facts, graph]):
        if provenance["type"] == "derived":
            assert set(provenance["member_ids"]) <= approved


def assert_provenance_integrity(case: dict[str, Any]) -> None:
    observations = observation_index(case["collection"])
    authoritative = authoritative_index(case["collection"])
    values = [case["expected_facts"], case["expected_presentation_graph"]]
    for provenance in iter_provenances(values):
        assert_provenance_resolves(provenance, observations, authoritative)


def iter_json_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from iter_json_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_strings(child)
    elif isinstance(value, str):
        yield value


def assert_no_unresolved_graph(graph: dict[str, Any]) -> None:
    assert all(not item.startswith("unresolved:") for item in iter_json_strings(graph))


def assert_case_output_binding(case: dict[str, Any]) -> None:
    collection = case["collection"]
    facts = case["expected_facts"]
    graph = case["expected_presentation_graph"]
    manifests, _, _ = resource_closure_indexes(collection)
    graph_scope_ids = {item["id"] for item in graph["scopes"]}
    assert facts["snapshot_id"] == collection["snapshot_id"]
    for item in iter_facts(facts):
        assert set(item["resource_ids"]) <= set(manifests)
    for network in facts["segments"]:
        assert set(network["attributes"].get("scope_ids", [])) <= graph_scope_ids


@pytest.mark.parametrize(
    "fixture_path", GOLDEN_FILES, ids=lambda path: path.parent.name
)
def test_unresolved_references_never_enter_presentation_graph(
    fixture_path: Path,
) -> None:
    graph = load_case(fixture_path)["expected_presentation_graph"]

    assert_no_unresolved_graph(graph)


@pytest.mark.parametrize(
    "fixture_path", GOLDEN_FILES, ids=lambda path: path.parent.name
)
def test_golden_fact_graph_and_derived_references_are_closed(
    fixture_path: Path,
) -> None:
    case = load_case(fixture_path)
    facts = case["expected_facts"]
    graph = case["expected_presentation_graph"]

    assert_case_output_binding(case)
    assert_fact_reference_integrity(facts)
    assert_graph_reference_integrity(graph, facts)
    assert_derived_member_integrity(case)
    assert_provenance_integrity(case)


@pytest.mark.parametrize("case_name", sorted(EXPECTED_FACT_IDS))
def test_topology_bearing_successes_have_complete_expected_fact_sets(
    case_name: str,
) -> None:
    facts = load_case(FIXTURE_ROOT / case_name / "full-golden-v1.json")[
        "expected_facts"
    ]

    assert EXPECTED_FACT_IDS[case_name] <= set(fact_index(facts))


@pytest.mark.parametrize("case_name", sorted(EXPECTED_GRAPH_IDS))
def test_resolved_and_authoritative_graph_projections_are_frozen(
    case_name: str,
) -> None:
    graph = named_case(case_name)["expected_presentation_graph"]
    graph_ids = {item["id"] for item in graph["scopes"]}
    graph_ids.update(item["id"] for item in graph["entities"])
    graph_ids.update(item["id"] for item in graph["relations"])

    assert EXPECTED_GRAPH_IDS[case_name] <= graph_ids


def test_adc_cluster_projects_as_one_logical_entity() -> None:
    graph = named_case("adc_lb")["expected_presentation_graph"]
    entity_ids = {item["id"] for item in graph["entities"]}
    relation_ids = {item["id"] for item in graph["relations"]}
    assert "device-group:adc-cluster-1" in entity_ids
    assert {"device:adc-lb-1", "device:adc-lb-2"}.isdisjoint(entity_ids)
    assert "relation:adc-cluster-1" not in relation_ids
    assert all(
        item["relation_type"] != "logical_dependency" for item in graph["relations"]
    )
    projected = {
        item["id"]: {endpoint["ref_id"] for endpoint in item["endpoints"]}
        for item in graph["relations"]
    }
    assert "device-group:adc-cluster-1" in projected["relation:adc-lb-1:vip-https"]
    assert "device-group:adc-cluster-1" in projected["relation:synthetic-circuit-001"]
    assert [item["summary"] for item in graph["scopes"]] == [
        {
            "key_device_count": 1,
            "network_segment_count": 0,
            "child_scope_count": 1,
            "cross_scope_relation_count": 1,
        },
        {
            "key_device_count": 0,
            "network_segment_count": 1,
            "child_scope_count": 0,
            "cross_scope_relation_count": 0,
        },
    ]


def named_case(case_name: str) -> dict[str, Any]:
    return load_case(FIXTURE_ROOT / case_name / "full-golden-v1.json")


def test_duplicate_observation_ownership_fails_closed() -> None:
    case = named_case("huawei")
    duplicate = deepcopy(case["collection"]["sources"][0]["resources"][0])
    duplicate["resource_id"] = "different-owner"
    case["collection"]["sources"][0]["resources"].append(duplicate)

    with pytest.raises(AssertionError, match="duplicate observation"):
        observation_index(case["collection"])


def test_observed_provenance_wrong_owner_fails_closed() -> None:
    case = named_case("huawei")
    provenance = case["expected_facts"]["provenance_catalog"][0]["provenance"]
    provenance["resource_id"] = "different-owner"

    with pytest.raises(AssertionError):
        assert_provenance_integrity(case)


def test_manifest_profile_drift_fails_closed() -> None:
    case = named_case("huawei")
    manifest = case["collection"]["target_manifest"]["expected_resources"][0]
    manifest["optional_kinds"].pop()

    with pytest.raises(AssertionError):
        assert_manifest_profile_closure(case["collection"])


def test_unmanifested_resource_fails_closed() -> None:
    case = named_case("huawei")
    collection = case["collection"]
    resource = deepcopy(collection["sources"][0]["resources"][0])
    coverage = deepcopy(collection["completeness"]["observation_coverage"][0])
    resource["resource_id"] = coverage["resource_id"] = "extra-resource"
    collection["sources"][0]["resources"].append(resource)
    collection["completeness"]["observation_coverage"].append(coverage)

    with pytest.raises(AssertionError):
        assert_manifest_profile_closure(collection)


@pytest.mark.parametrize("target", ["source", "coverage"])
def test_missing_resource_closure_fails_closed(target: str) -> None:
    collection = named_case("huawei")["collection"]
    if target == "source":
        collection["sources"][0]["resources"].clear()
    else:
        collection["completeness"]["observation_coverage"].clear()

    with pytest.raises(AssertionError):
        assert_manifest_profile_closure(collection)


@pytest.mark.parametrize("target", ["manifest", "source", "coverage"])
def test_duplicate_resource_closure_fails_closed(target: str) -> None:
    collection = named_case("huawei")["collection"]
    collections = {
        "manifest": collection["target_manifest"]["expected_resources"],
        "source": collection["sources"][0]["resources"],
        "coverage": collection["completeness"]["observation_coverage"],
    }
    collections[target].append(deepcopy(collections[target][0]))

    with pytest.raises(AssertionError, match="duplicate"):
        assert_manifest_profile_closure(collection)


@pytest.mark.parametrize("target", ["fact", "unresolved"])
def test_dangling_provenance_ids_fail_closed(target: str) -> None:
    case = named_case("huawei")
    facts = case["expected_facts"]
    item = (
        facts["devices"][0] if target == "fact" else facts["unresolved_references"][0]
    )
    item["provenance_ids"].append("missing-provenance")

    with pytest.raises(AssertionError):
        assert_fact_reference_integrity(facts)


def test_dangling_typed_fact_reference_fails_closed() -> None:
    facts = named_case("huawei")["expected_facts"]
    facts["memberships"][0]["attributes"]["container_id"] = "missing-fact"

    with pytest.raises(AssertionError):
        assert_fact_reference_integrity(facts)


def test_dangling_observed_provenance_fails_closed() -> None:
    case = named_case("huawei")
    provenance = case["expected_facts"]["provenance_catalog"][0]["provenance"]
    provenance["observation_id"] = "missing-observation"

    with pytest.raises(AssertionError):
        assert_provenance_integrity(case)


@pytest.mark.parametrize("target", ["endpoint", "fact_member", "relation_member"])
def test_dangling_graph_references_fail_closed(target: str) -> None:
    case = named_case("adc_lb")
    graph = case["expected_presentation_graph"]
    relation_item = graph["relations"][0]
    if target == "endpoint":
        relation_item["endpoints"][0]["ref_id"] = "missing-entity"
    elif target == "fact_member":
        relation_item["member_fact_ids"] = ["missing-fact"]
    else:
        relation_item["member_relation_ids"] = ["missing-relation"]

    with pytest.raises(AssertionError):
        assert_graph_reference_integrity(graph, case["expected_facts"])


def test_dangling_derived_member_fails_closed() -> None:
    case = named_case("adc_lb")
    case["expected_presentation_graph"]["scopes"][0]["provenance"][0]["member_ids"] = [
        "missing-derived-member"
    ]

    with pytest.raises(AssertionError):
        assert_derived_member_integrity(case)


@pytest.mark.parametrize("target", ["entity_id", "nested_attribute", "derived_member"])
def test_unresolved_graph_projection_fails_closed(target: str) -> None:
    graph = named_case("huawei")["expected_presentation_graph"]
    if target == "entity_id":
        graph["entities"][0]["id"] = "unresolved:forbidden-graph-entity"
    elif target == "nested_attribute":
        graph["entities"][0]["attributes"]["network_prefixes"] = [
            "unresolved:nested-attribute"
        ]
    else:
        graph["scopes"][0]["provenance"][0]["member_ids"] = [
            "unresolved:derived-member"
        ]

    with pytest.raises(AssertionError):
        assert_no_unresolved_graph(graph)


@pytest.mark.parametrize("target", ["snapshot", "fact_resource", "network_scope"])
def test_case_output_binding_mutations_fail_closed(target: str) -> None:
    case = named_case("adc_lb")
    facts = case["expected_facts"]
    if target == "snapshot":
        facts["snapshot_id"] = "snapshot:wrong"
        case["expected_presentation_graph"]["scopes"][0]["provenance"][0][
            "member_ids"
        ] = ["snapshot:wrong"]
    elif target == "fact_resource":
        facts["devices"][0]["resource_ids"] = ["missing-resource"]
    else:
        network = next(
            item for item in facts["segments"] if "scope_ids" in item["attributes"]
        )
        network["attributes"]["scope_ids"] = ["scope:missing"]

    with pytest.raises(AssertionError):
        assert_case_output_binding(case)


def test_golden_fixture_discovery_and_scenario_coverage() -> None:
    assert {path.parent.name for path in GOLDEN_FILES} == EXPECTED_CASES
    cases = {path.parent.name: load_case(path) for path in GOLDEN_FILES}
    expected_kinds = {
        "huawei": {"l2.neighbors", "l3.vrfs"},
        "h3c": {"l2.vlans", "l3.connected_networks"},
        "cisco": {"routing.connected_static_default"},
        "fortinet": {"security.zones", "l3.vrfs", "overlay.tunnels"},
        "adc_lb": {
            "system.cluster_members",
            "service.virtual_addresses",
            "routing.connected_static_default",
        },
    }
    for name, kinds in expected_kinds.items():
        observed = set().union(
            *observed_kinds_by_resource(cases[name]["collection"]).values()
        )
        assert {"system.identity", "interface.inventory", *kinds} <= observed
    authority = cases["adc_lb"]["collection"]["authoritative_context"]
    assert all(
        authority[family]
        for family in ("external_networks", "circuits", "cloud_networks")
    )


@pytest.mark.parametrize(
    "fixture_path", GOLDEN_FILES, ids=lambda path: path.parent.name
)
def test_full_collection_and_expected_outputs_are_schema_valid(
    fixture_path: Path,
) -> None:
    case = load_case(fixture_path)
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
    kinds = observed_kinds_by_resource(case["collection"])
    for resource in case["collection"]["target_manifest"]["expected_resources"]:
        assert set(resource["required_kinds"]) <= kinds[resource["resource_id"]]


@pytest.mark.parametrize(
    "fixture_path", GOLDEN_FILES, ids=lambda path: path.parent.name
)
def test_golden_observation_and_canonical_hashes(fixture_path: Path) -> None:
    case = load_case(fixture_path)
    for observation in iter_observations(case["collection"]):
        validate_observation_hashes(observation)
    hashes = case["canonical_hashes"]
    assert (
        canonicalize_and_hash_snapshot(case["collection"]).payload_hash
        == hashes["snapshot"]
    )
    assert sha256_uri(canonicalize_json(case["expected_facts"])) == hashes["facts"]
    assert (
        sha256_uri(canonicalize_json(case["expected_presentation_graph"]))
        == (hashes["graph"])
    )


@pytest.mark.parametrize(
    "fixture_path", GOLDEN_FILES, ids=lambda path: path.parent.name
)
def test_golden_unresolved_and_provenance_locators(fixture_path: Path) -> None:
    case = load_case(fixture_path)
    facts = case["expected_facts"]
    unresolved = [
        {"id": item["id"], "reason_code": item["reason_code"]}
        for item in facts["unresolved_references"]
    ]
    assert unresolved == case["expected_unresolved_references"]
    catalog = {item["id"]: item["provenance"] for item in facts["provenance_catalog"]}
    expected = {
        item["provenance_id"]: item["expected_value"]
        for item in case["provenance_expectations"]
    }
    assert set(catalog) == set(expected)
    observations = observation_index(case["collection"])
    authoritative = authoritative_index(case["collection"])
    for provenance_id, provenance in catalog.items():
        assert (
            assert_provenance_resolves(provenance, observations, authoritative)
            == expected[provenance_id]
        )
    for provenance in iter_provenances(case["expected_presentation_graph"]):
        assert_provenance_resolves(provenance, observations, authoritative)
