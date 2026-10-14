from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from referencing.exceptions import Unresolvable

from services.network_topology import contracts

STANDARD_OBSERVATION_KINDS = {
    "system.identity",
    "system.virtual_contexts",
    "system.cluster_members",
    "interface.inventory",
    "l2.vlans",
    "l2.link_aggregation",
    "l2.neighbors",
    "l2.stp_summary",
    "l3.vrfs",
    "l3.connected_networks",
    "routing.connected_static_default",
    "routing.dynamic_neighbors",
    "routing.first_hop_redundancy",
    "security.zones",
    "security.zone_bindings",
    "overlay.tunnels",
    "overlay.vxlan_evpn",
    "wan.sdwan",
    "ipam.subnets",
    "service.virtual_addresses",
    "l3.neighbor_cache",
    "legacy.running_config",
    "raw.action_output",
}
NEW_STANDARD_OBSERVATION_KINDS = {
    "system.virtual_contexts",
    "l2.stp_summary",
    "ipam.subnets",
    "legacy.running_config",
}
SHA256 = "sha256:" + "0" * 64

FORMAL_FACT_CASES = (
    (
        "devices",
        "device",
        {
            "device_type": "router",
            "identity_aliases": [],
            "roles": ["router"],
            "inventory_state": "managed",
        },
    ),
    (
        "device_groups",
        "device_group",
        {"group_type": "ha", "identity": "ha-1", "member_ids": ["device:1"]},
    ),
    (
        "network_contexts",
        "network_context",
        {
            "owner_id": "device:1",
            "local_id": "context:root",
            "name": "root",
            "context_type": "physical",
        },
    ),
    (
        "network_contexts",
        "vrf",
        {
            "owner_id": "device:1",
            "local_id": "vrf:prod",
            "name": "PROD",
            "rd": None,
            "import_route_targets": [],
            "export_route_targets": [],
            "interface_ids": [],
        },
    ),
    (
        "network_contexts",
        "vlan",
        {
            "owner_id": "device:1",
            "local_id": "vlan:100",
            "name": "PROD",
            "vlan_id": 100,
            "bridge_domain": None,
            "vni": None,
            "interface_ids": [],
        },
    ),
    (
        "network_contexts",
        "zone",
        {
            "owner_id": "device:1",
            "local_id": "zone:trust",
            "name": "trust",
            "interface_ids": [],
        },
    ),
    (
        "interfaces",
        "interface",
        {
            "device_id": "device:1",
            "context_id": None,
            "canonical_name": "GigabitEthernet0/0/1",
            "raw_name": "GE0/0/1",
            "if_index": 1,
            "mac": None,
            "parent_interface_id": None,
            "lag_id": None,
            "addresses": [],
            "configured_state": "up",
            "operational_state": "up",
        },
    ),
    (
        "segments",
        "network",
        {
            "canonical_cidr": "192.0.2.0/24",
            "network_domain_id": "domain:default",
            "device_id": "device:1",
            "context_id": None,
            "vrf_id": None,
            "vlan_id": None,
            "vni": None,
            "gateway_ids": [],
        },
    ),
    (
        "adjacencies",
        "neighbor",
        {
            "neighbor_type": "lldp",
            "local_id": "device:1",
            "remote_id": "device:2",
            "attachments": ["interface:1", "interface:2"],
            "layer": "physical",
            "context_id": None,
            "state": "up",
        },
    ),
    (
        "routes",
        "route",
        {
            "route_type": "default",
            "prefix": "0.0.0.0/0",
            "vrf_id": "vrf:default",
            "next_hop_id": None,
            "interface_id": None,
            "state": "active",
        },
    ),
    (
        "routing_relationships",
        "routing_relationship",
        {
            "protocol": "bgp",
            "local_id": "device:1",
            "remote_id": "device:2",
            "vrf_id": "vrf:default",
            "session_state": "established",
        },
    ),
    (
        "tunnels",
        "tunnel",
        {
            "tunnel_type": "ipsec",
            "tunnel_id": "tunnel:1",
            "context_id": None,
            "interface_id": None,
            "local_endpoint": "192.0.2.1",
            "remote_endpoint": "198.51.100.1",
            "protected_prefixes": [],
            "state": "up",
        },
    ),
    (
        "memberships",
        "membership",
        {
            "member_id": "device:1",
            "container_id": "group:1",
            "membership_kind": "ha_member",
        },
    ),
    (
        "gateways",
        "gateway",
        {
            "gateway_id": "interface:1",
            "segment_id": "segment:1",
            "gateway_kind": "interface",
        },
    ),
    (
        "security_boundaries",
        "security",
        {
            "device_id": "device:1",
            "interface_id": "interface:1",
            "zone_id": "zone:trust",
            "configured_state": "present",
            "operational_state": "up",
        },
    ),
    (
        "wan_connections",
        "wan",
        {
            "connection_type": "circuit",
            "local_id": "device:1",
            "remote_id": "external:1",
            "state": "up",
        },
    ),
)


def schema_example(schema_uri: str) -> dict[str, Any]:
    schema = contracts.load_schema(schema_uri)
    return deepcopy(schema["examples"][0])


def assert_rejected(payload: Any, schema_uri: str) -> None:
    with pytest.raises(contracts.ContractError):
        contracts.validate_contract(payload, schema_uri)


def clear_contract_caches() -> None:
    for loader in (
        contracts._schema_catalog,
        contracts._schema_registry,
        contracts._registry_catalog,
        contracts._validated_registry_summary,
    ):
        loader.cache_clear()


def smoke_with_extra_schema(
    monkeypatch: pytest.MonkeyPatch, extra_schema: dict[str, Any]
) -> dict[str, int]:
    packaged = list(contracts.iter_schemas())
    monkeypatch.setattr(
        contracts,
        "iter_schemas",
        lambda: iter([*packaged, deepcopy(extra_schema)]),
    )
    clear_contract_caches()
    try:
        return contracts.smoke_all_contracts()
    finally:
        clear_contract_caches()


def iter_refs(value: Any):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for child in value.values():
            yield from iter_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_refs(child)


def constrained_values(property_schema: dict[str, Any]) -> set[str]:
    if "const" in property_schema:
        return {property_schema["const"]}
    return set(property_schema["enum"])


def test_all_packaged_schemas_are_valid_and_refs_resolve() -> None:
    schemas = list(contracts.iter_schemas())
    registry = contracts._schema_registry()

    assert schemas
    for schema in schemas:
        base_uri = schema["$id"]
        schema_uri = base_uri.removeprefix(contracts.SCHEMA_ID_BASE)
        assert schema["$schema"] == contracts.JSON_SCHEMA_DRAFT_URI
        assert contracts.load_schema(schema_uri) == schema
        resolver = registry.resolver(base_uri)
        for ref in iter_refs(schema):
            try:
                resolver.lookup(ref)
            except Unresolvable as exc:
                pytest.fail(f"{base_uri} has unresolvable ref {ref}: {exc}")

    result = contracts.smoke_all_contracts()
    assert result["schema_count"] == len(schemas)
    assert result["example_count"] > 0


def test_contract_smoke_rejects_non_2020_12_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incompatible = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": contracts.SCHEMA_ID_BASE + "aioe.network-topology.probe/v1",
        "type": "object",
    }
    with pytest.raises(contracts.ContractError, match="must declare JSON Schema"):
        smoke_with_extra_schema(monkeypatch, incompatible)


@pytest.mark.parametrize("ref_keyword", ["$ref", "$dynamicRef"])
def test_contract_smoke_rejects_unresolvable_schema_reference(
    monkeypatch: pytest.MonkeyPatch, ref_keyword: str
) -> None:
    dangling = {
        "$schema": contracts.JSON_SCHEMA_DRAFT_URI,
        "$id": contracts.SCHEMA_ID_BASE + "aioe.network-topology.probe/v1",
        ref_keyword: contracts.SCHEMA_ID_BASE + "aioe.network-topology.missing/v1",
    }
    with pytest.raises(contracts.ContractError, match="Unresolvable schema reference"):
        smoke_with_extra_schema(monkeypatch, dangling)


def test_every_standard_observation_has_an_independent_strict_schema() -> None:
    schemas = {
        schema["$id"].rsplit("/", 2)[-2]: schema
        for schema in contracts.iter_schemas()
        if ".observation/" in schema["$id"]
        and not schema["$id"].endswith("/vendor-extension/v1")
    }

    assert set(schemas) == STANDARD_OBSERVATION_KINDS
    for kind, schema in schemas.items():
        assert schema["x-max-bytes"] > 0
        assert schema["x-allowed-source-methods"]
        assert schema["x-fact-planes"]
        assert schema.get("unevaluatedProperties") is False
        assert schema["$id"].endswith(f"/{kind}/v1")
        observation_properties = schema["allOf"][1]["properties"]
        assert set(schema["x-allowed-source-methods"]) == constrained_values(
            observation_properties["source_method"]
        )
        assert set(schema["x-fact-planes"]) == constrained_values(
            observation_properties["fact_plane"]
        )


def test_new_standard_observations_allow_empty_results_and_reject_extra_data() -> None:
    collection_uri = "aioe.network-topology.collection/v2"
    for kind in NEW_STANDARD_OBSERVATION_KINDS:
        observation_uri = f"aioe.network-topology.observation/{kind}/v1"
        observation = schema_example(observation_uri)
        contracts.validate_contract(observation, observation_uri)

        collection = schema_example(collection_uri)
        collection["sources"][0]["resources"][0]["result"]["observations"][0] = (
            observation
        )
        contracts.validate_contract(collection, collection_uri)

        invalid = deepcopy(observation)
        invalid["data"]["invented_collection"] = []
        assert_rejected(invalid, observation_uri)

    legacy = schema_example(
        "aioe.network-topology.observation/legacy.running_config/v1"
    )
    legacy["source_method"] = "vendor_api"
    assert_rejected(
        legacy, "aioe.network-topology.observation/legacy.running_config/v1"
    )


def test_representative_embedded_examples_validate() -> None:
    for schema in contracts.iter_schemas():
        schema_uri = schema["$id"].removeprefix(contracts.SCHEMA_ID_BASE)
        for example in schema.get("examples", []):
            contracts.validate_contract(deepcopy(example), schema_uri)


def test_collection_rejects_missing_extra_and_unknown_observation() -> None:
    uri = "aioe.network-topology.collection/v2"
    payload = schema_example(uri)
    contracts.validate_versioned_contract(payload)

    missing = deepcopy(payload)
    missing.pop("target_manifest")
    assert_rejected(missing, uri)

    extra = deepcopy(payload)
    extra["tenant_id"] = "forbidden"
    assert_rejected(extra, uri)

    unknown = deepcopy(payload)
    observation = unknown["sources"][0]["resources"][0]["result"]["observations"][0]
    observation["kind"] = "other"
    observation["data_schema"] = "aioe.network-topology.observation/other/v1"
    assert_rejected(unknown, uri)


def test_unknown_and_aliased_versions_fail_closed() -> None:
    with pytest.raises(contracts.ContractError, match="Unsupported schema URI"):
        contracts.load_schema("aioe.network-topology.collection/v3")
    with pytest.raises(contracts.ContractError, match="Unsupported schema URI"):
        contracts.load_schema(
            "https://schemas.aioe.local/aioe.network-topology.collection/v2"
        )

    payload = schema_example("aioe.network-topology.collection/v2")
    payload["schema_version"] = "aioe.network-topology.collection/v3"
    with pytest.raises(contracts.ContractError, match="Unsupported schema URI"):
        contracts.validate_versioned_contract(payload)


def test_candidates_reject_server_bound_ids_and_coordinates() -> None:
    uri = "aioe.network-topology.llm-fact-candidates/v1"
    base = schema_example(uri)
    forbidden_fields = (
        "snapshot_id",
        "resource_id",
        "analysis_run_id",
        "stable_id",
        "scope_parent_id",
        "revision",
        "x",
        "y",
    )
    for field in forbidden_fields:
        payload = deepcopy(base)
        payload[field] = "forbidden"
        assert_rejected(payload, uri)

    device = {
        "candidate_id": "device-1",
        "device_type": "router",
        "identity_aliases": [],
        "roles": [],
        "inventory_state": "managed",
        "evidence_refs": [
            {
                "observation_id": "resource-1:identity:1",
                "locator": {"type": "json_pointer", "pointer": "/hostname"},
            }
        ],
    }
    payload = deepcopy(base)
    payload["devices"] = [device]
    contracts.validate_contract(payload, uri)
    for field in ("stable_id", "database_id", "scope_parent_id", "x", "y"):
        payload = deepcopy(base)
        payload["devices"] = [{**device, field: "forbidden"}]
        assert_rejected(payload, uri)


def test_formal_facts_reject_unregistered_attributes() -> None:
    uri = "aioe.network-topology.facts/v1"
    payload = schema_example(uri)
    payload["devices"] = [
        {
            "id": "device:resource-1",
            "fact_family": "device",
            "resource_ids": ["resource-1"],
            "source_candidate_ids": ["candidate-1"],
            "attributes": {"invented_control": True},
            "provenance_ids": ["provenance-1"],
            "parsing_state": "verified",
        }
    ]
    assert_rejected(payload, uri)


@pytest.mark.parametrize(("collection", "family", "attributes"), FORMAL_FACT_CASES)
def test_formal_facts_use_strict_family_attributes(
    collection: str, family: str, attributes: dict[str, Any]
) -> None:
    uri = "aioe.network-topology.facts/v1"
    payload = schema_example(uri)
    fact = {
        "id": f"{family}:1",
        "fact_family": family,
        "resource_ids": ["resource-1"],
        "source_candidate_ids": ["candidate-1"],
        "attributes": attributes,
        "provenance_ids": ["provenance-1"],
        "parsing_state": "verified",
    }
    payload[collection] = [fact]
    contracts.validate_contract(payload, uri)

    invalid = deepcopy(payload)
    invalid[collection][0]["attributes"]["invented_control"] = True
    assert_rejected(invalid, uri)


def service_boundary_graph() -> dict[str, Any]:
    uri = "aioe.network-topology.graph/v2"
    payload = schema_example(uri)
    payload["entities"] = [
        {
            "id": "service-boundary:1",
            "scope_id": "scope:organization:root",
            "scope_memberships": [],
            "kind": "service_boundary",
            "name": "Synthetic Service Boundary",
            "role": "service_boundary",
            "representation": "service_boundary",
            "importance": "important",
            "importance_reasons": ["authoritative_profile_exception"],
            "attributes": {
                "boundary_type": "vip",
                "network_prefixes": ["192.0.2.0/24"],
                "virtual_addresses": ["192.0.2.10"],
                "protocols": ["tcp"],
                "ports": [443],
            },
            "provenance": [
                {
                    "type": "authoritative",
                    "source_system": "synthetic-cmdb",
                    "source_record_id": "service-1",
                    "source_revision": "rev-1",
                    "as_of": "2026-07-28T00:00:00Z",
                    "field_locator": "/services/0",
                }
            ],
        }
    ]
    return payload


def logical_dependency_graph(provenance: dict[str, Any]) -> dict[str, Any]:
    payload = schema_example("aioe.network-topology.graph/v2")
    payload["relations"] = [
        {
            "id": "relation:logical-dependency-1",
            "multi_edge_identity": {
                "endpoint_key": "device-group:1|device-group:2",
                "layer": "physical",
                "relation_type": "logical_dependency",
                "attachment_key": "",
                "context_key": "scope:organization:root",
            },
            "relation_type": "logical_dependency",
            "layer": "physical",
            "direction": "bidirectional",
            "endpoints": [
                {"ref_type": "entity", "ref_id": "device-group:1"},
                {"ref_type": "entity", "ref_id": "device-group:2"},
            ],
            "owner_scope_id": "scope:organization:root",
            "attributes": {},
            "member_fact_ids": [],
            "member_relation_ids": [],
            "configured_state": "present",
            "operational_state": "unknown",
            "provenance": [provenance],
        }
    ]
    return payload


def manifest_request() -> dict[str, Any]:
    return {
        "id": "topology-1",
        "profile_revision": 7,
        "resolved_resource_ids": ["asset-fw-001"],
        "selection_provenance": {
            "playbook_id": "pb-network-discovery",
            "run_id": "run-001",
            "selector_hash": SHA256,
        },
    }


def test_graph_service_boundary_is_minimal_and_authoritative() -> None:
    uri = "aioe.network-topology.graph/v2"
    payload = service_boundary_graph()
    contracts.validate_contract(payload, uri)

    for inventory_field in ("management_ip", "vendor", "model", "serial"):
        with_device_inventory = deepcopy(payload)
        with_device_inventory["entities"][0]["attributes"][inventory_field] = (
            "forbidden"
        )
        assert_rejected(with_device_inventory, uri)

    observed_only = deepcopy(payload)
    observed_only["entities"][0]["provenance"] = [
        {
            "type": "observed",
            "resource_id": "resource-1",
            "observation_id": "resource-1:identity:1",
            "redacted_sha256": SHA256,
            "locator": {"type": "json_pointer", "pointer": "/hostname"},
        }
    ]
    assert_rejected(observed_only, uri)

    profile_approved = deepcopy(payload)
    profile_approved["entities"][0]["provenance"] = [
        {
            "type": "derived",
            "rule_id": "profile.explicit_asset_exception/v1",
            "profile_revision": 7,
            "member_ids": ["authoritative-asset:1"],
        }
    ]
    contracts.validate_contract(profile_approved, uri)

    arbitrary_derived = deepcopy(profile_approved)
    arbitrary_derived["entities"][0]["provenance"][0]["rule_id"] = (
        "projection.generic/v1"
    )
    assert_rejected(arbitrary_derived, uri)


def test_logical_dependency_requires_authoritative_or_manual_provenance() -> None:
    uri = "aioe.network-topology.graph/v2"
    authoritative = {
        "type": "authoritative",
        "source_system": "synthetic-cmdb",
        "source_record_id": "dependency-1",
        "source_revision": "rev-1",
        "as_of": "2026-07-28T00:00:00Z",
        "field_locator": "/dependencies/0",
    }
    manual = {
        "type": "derived",
        "rule_id": "manual.logical-dependency/v1",
        "profile_revision": 1,
        "member_ids": ["device-group:1", "device-group:2"],
        "manual_override_ref": "manual-override:1",
    }
    for provenance in (authoritative, manual):
        contracts.validate_contract(logical_dependency_graph(provenance), uri)

    observed = {
        "type": "observed",
        "resource_id": "resource-1",
        "observation_id": "resource-1:cluster:1",
        "redacted_sha256": SHA256,
        "locator": {"type": "json_pointer", "pointer": "/group_id"},
    }
    assert_rejected(logical_dependency_graph(observed), uri)
    missing_manual_override = deepcopy(manual)
    missing_manual_override.pop("manual_override_ref")
    assert_rejected(logical_dependency_graph(missing_manual_override), uri)

    unrelated_relation = logical_dependency_graph(observed)
    relation_item = unrelated_relation["relations"][0]
    relation_item["relation_type"] = "physical_link"
    relation_item["multi_edge_identity"]["relation_type"] = "physical_link"
    contracts.validate_contract(unrelated_relation, uri)


def test_api_manifest_profile_and_reanalyze_shapes_match_frozen_contract() -> None:
    manifest_uri = "aioe.network-topology.api/manifest-request/v1"
    manifest = manifest_request()
    contracts.validate_contract(manifest, manifest_uri)

    profile_contracts = (
        ("request", "reanalyze_now", True),
        ("response", "reanalyze_sync_id", "analysis-2"),
    )
    for suffix, legacy_field, legacy_value in profile_contracts:
        uri = f"aioe.network-topology.api/profile-{suffix}/v1"
        payload = schema_example(uri)
        contracts.validate_contract(payload, uri)
        target = payload["data"] if suffix == "response" else payload
        target[legacy_field] = legacy_value
        assert_rejected(payload, uri)

    reanalyze_uri = "aioe.network-topology.api/reanalyze-request/v1"
    reanalyze = schema_example(reanalyze_uri)
    for uri in (reanalyze_uri, "aioe.network-topology.api/reanalyze-response/v1"):
        contracts.validate_contract(schema_example(uri), uri)

    legacy_manifest = deepcopy(manifest)
    legacy_manifest["selection_provenance"] = [
        {
            "type": "authoritative",
            "source_system": "cmdb",
            "source_record_id": "selection-1",
            "source_revision": "revision-1",
            "as_of": "2026-07-28T00:00:00Z",
            "field_locator": "/resources",
        }
    ]
    assert_rejected(legacy_manifest, manifest_uri)

    legacy_reanalyze = deepcopy(reanalyze)
    legacy_reanalyze["profile_revision"] = legacy_reanalyze.pop(
        "target_profile_revision"
    )
    assert_rejected(legacy_reanalyze, "aioe.network-topology.api/reanalyze-request/v1")

    for required_field in ("publish_as_current", "expected_current_graph_revision"):
        missing = deepcopy(reanalyze)
        missing.pop(required_field)
        assert_rejected(missing, "aioe.network-topology.api/reanalyze-request/v1")


def test_upload_start_chunk_commit_fields_are_separated() -> None:
    collection = schema_example("aioe.network-topology.collection/v2")
    observation = deepcopy(
        collection["sources"][0]["resources"][0]["result"]["observations"][0]
    )
    envelope = deepcopy(collection)
    for source in envelope["sources"]:
        for resource in source["resources"]:
            resource["result"] = {
                "status": "success",
                "expected_observation_count": len(
                    collection["sources"][0]["resources"][0]["result"]["observations"]
                ),
            }

    start = {
        "id": "topology-1",
        "manifest_id": envelope["target_manifest"]["manifest_id"],
        "snapshot_envelope_without_observations": envelope,
        "client_expected_input_hash": SHA256,
    }
    contracts.validate_contract(
        start, "aioe.network-topology.api/upload-start-request/v1"
    )
    bad_start = deepcopy(start)
    bad_start["snapshot_envelope_without_observations"]["sources"][0]["resources"][0][
        "result"
    ]["observations"] = [observation]
    assert_rejected(bad_start, "aioe.network-topology.api/upload-start-request/v1")

    chunk = {
        "upload_session_id": "upload-1",
        "resource_id": "synthetic-router-1",
        "observation": observation,
    }
    contracts.validate_contract(
        chunk, "aioe.network-topology.api/upload-chunk-request/v1"
    )
    bad_chunk = {**chunk, "observations": [observation]}
    assert_rejected(bad_chunk, "aioe.network-topology.api/upload-chunk-request/v1")

    commit = {"upload_session_id": "upload-1", "client_expected_input_hash": SHA256}
    contracts.validate_contract(
        commit, "aioe.network-topology.api/upload-commit-request/v1"
    )
    assert_rejected(
        {**commit, "snapshot": collection},
        "aioe.network-topology.api/upload-commit-request/v1",
    )


def test_status_contract_enforces_terminal_and_blocked_state_invariants() -> None:
    uri = "aioe.network-topology.api/status-response/v1"
    payload = schema_example(uri)
    payload["data"]["state"] = "SUCCEEDED"
    payload["data"]["terminal"] = False
    assert_rejected(payload, uri)

    for example in contracts.load_schema(uri)["examples"]:
        contracts.validate_contract(deepcopy(example), uri)

    waiting = deepcopy(contracts.load_schema(uri)["examples"][1])
    waiting["data"]["blocked_by"] = None
    assert_rejected(waiting, uri)

    running = schema_example(uri)
    running["data"]["blocked_by"] = "PUBLISH_GATE"
    assert_rejected(running, uri)

    terminal = deepcopy(contracts.load_schema(uri)["examples"][3])
    terminal["data"]["terminal"] = False
    assert_rejected(terminal, uri)

