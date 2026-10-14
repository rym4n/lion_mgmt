from copy import deepcopy

import pytest

from services.network_topology import contracts
from services.network_topology.run_pipeline import build_collection_chunks

RAW = {
    "observation_id": "obs-1",
    "kind": "raw.action_output",
    "observation_status": "success",
    "source_method": "aioe_action",
    "content_type": "application/json",
    "data_schema": "aioe.network-topology.observation/raw.action_output/v1",
    "fact_plane": "operational",
    "command_or_api": "huawei_switch.collect_topology_observations_v2",
    "observed_at": "2026-10-14T00:00:00Z",
    "data": {
        "app_name": "huawei_switch",
        "action_func": "collect_topology_observations_v2",
        "app_version": "abc",
        "resource_id": "res-1",
        "output": {"records": [{"command_or_api": "display version", "data": "VRP"}]},
    },
    "raw_sha256": "sha256:" + "0" * 64,
    "content_sha256": "sha256:" + "0" * 64,
}


def test_registry_integrity_includes_raw_action_output():
    contracts.validate_registry_integrity()
    kinds = contracts.load_registry(
        "aioe.network-topology.registry/observation-kinds/v1"
    )["kinds"]
    assert {
        "kind": "raw.action_output",
        "schema_uri": "aioe.network-topology.observation/raw.action_output/v1",
    } in kinds


def test_raw_observation_validates():
    contracts.validate_contract(
        RAW, "aioe.network-topology.observation/raw.action_output/v1"
    )


def test_aioe_action_source_method_rejected_for_typed_kinds():
    registry = contracts.load_registry(
        "aioe.network-topology.registry/observation-kinds/v1"
    )
    for item in registry["kinds"]:
        if item["kind"] == "raw.action_output":
            continue
        schema = contracts.load_schema(item["schema_uri"])
        typed = {
            **deepcopy(RAW),
            "kind": item["kind"],
            "data_schema": item["schema_uri"],
            "observation_status": "not_supported",
            "data": None,
            "reason_code": "NOT_SUPPORTED",
            "reason_summary": "Unsupported by this fixture",
            "source_method": schema["x-allowed-source-methods"][0],
            "fact_plane": schema["x-fact-planes"][0],
            "content_type": schema["allOf"][1]["properties"]["content_type"]["const"],
            "parser_context": {
                "vendor": "huawei",
                "platform": "fixture",
                "os_family": "vrp",
                "parser_profile": "fixture/v1",
                "encoding": "utf-8",
            },
        }
        contracts.validate_contract(typed, item["schema_uri"])
        typed["source_method"] = "aioe_action"
        with pytest.raises(contracts.ContractError):
            contracts.validate_contract(typed, item["schema_uri"])


def test_capability_profile_for_action_collection():
    registry = contracts.load_registry(
        "aioe.network-topology.registry/capability-profiles/v1"
    )
    profile = next(
        p for p in registry["profiles"] if p["id"] == "aioe-action-collection/v1"
    )
    assert profile["required_kinds"] == ["raw.action_output"]
    assert "legacy.running_config" not in profile["optional_kinds"]


def test_collection_accepts_single_raw_observation_and_chunks_raw_domain():
    collection = deepcopy(
        contracts.load_schema("aioe.network-topology.collection/v2")["examples"][0]
    )
    collection["sources"][0]["resources"][0]["result"]["observations"] = [deepcopy(RAW)]
    contracts.validate_contract(collection, "aioe.network-topology.collection/v2")
    chunks = build_collection_chunks(collection)
    assert len(chunks) == 1
    assert chunks[0].domain == "raw"
    assert chunks[0].observations == (RAW,)


@pytest.mark.parametrize("mutation", ["id", "match", "required", "optional"])
def test_raw_only_profile_exception_is_narrow(mutation):
    registry = contracts.load_registry(
        "aioe.network-topology.registry/capability-profiles/v1"
    )
    profile = next(
        p for p in registry["profiles"] if p["id"] == "aioe-action-collection/v1"
    )
    if mutation == "id":
        profile["id"] = "other/v1"
    elif mutation == "match":
        profile["match"]["vendor"] = "huawei"
    elif mutation == "required":
        profile["required_kinds"] = []
    else:
        profile["optional_kinds"].append("legacy.running_config")
    kinds = {
        p["kind"]
        for p in contracts.load_registry(
            "aioe.network-topology.registry/observation-kinds/v1"
        )["kinds"]
    }
    with pytest.raises(contracts.ContractError):
        contracts._validate_capability_registry(registry, kinds)


def test_other_capability_cannot_be_raw_only():
    registry = contracts.load_registry(
        "aioe.network-topology.registry/capability-profiles/v1"
    )
    registry["profiles"][0]["required_kinds"] = ["raw.action_output"]
    kinds = {
        p["kind"]
        for p in contracts.load_registry(
            "aioe.network-topology.registry/observation-kinds/v1"
        )["kinds"]
    }
    with pytest.raises(contracts.ContractError, match="Identity/interfaces"):
        contracts._validate_capability_registry(registry, kinds)
