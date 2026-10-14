from __future__ import annotations

from copy import deepcopy

import pytest

from services.network_topology import contracts

CAPABILITY_URI = "aioe.network-topology.registry/capability-profiles/v1"
OBSERVATION_URI = "aioe.network-topology.registry/observation-kinds/v1"
PARSER_URI = "aioe.network-topology.registry/parser-bundle/v1"
ACTION_URI = "aioe.network-topology.registry/vendor-action-mapping/v1"
BASE_REQUIRED = {"system.identity", "interface.inventory"}
EXPECTED_POLICY = {
    "prompt_policy_id": "aioe.network-topology.prompt/fact-candidates/v1",
    "model_selection": "deployment_allowlist",
    "tools_allowed": False,
    "network_allowed": False,
    "candidate_output_schema": "aioe.network-topology.llm-fact-candidates/v1",
    "chunk_input_schema": "aioe.network-topology.llm-chunk/v1",
    "formal_facts_schema": "aioe.network-topology.facts/v1",
}


def action_validation_inputs():
    kinds = {item["kind"] for item in contracts.load_registry(OBSERVATION_URI)["kinds"]}
    profiles = {
        item["id"]: item for item in contracts.load_registry(CAPABILITY_URI)["profiles"]
    }
    return profiles, kinds


def test_all_four_versioned_registries_load_and_validate() -> None:
    result = contracts.validate_registry_integrity()

    assert result == {
        "registry_count": 4,
        "observation_kind_count": 23,
        "capability_count": 7,
    }
    assert {item["schema_version"] for item in contracts.iter_registries()} == {
        OBSERVATION_URI,
        CAPABILITY_URI,
        PARSER_URI,
        ACTION_URI,
    }
    with pytest.raises(contracts.ContractError, match="Unsupported registry URI"):
        contracts.load_registry("aioe.network-topology.registry/capability-profiles/v2")


def test_default_profile_is_frozen_and_schema_valid() -> None:
    profile = contracts.load_registry(CAPABILITY_URI)["default_profile"]

    contracts.validate_contract(profile, "aioe.network-topology.profile/v1")
    assert profile["root_scope_seed"] == "scope:organization:root"
    assert profile["grouping_order"] == [
        "organization",
        "region",
        "site",
        "network_domain",
        "security_zone",
        "vrf",
    ]
    assert profile["access_device_policy"] == "collapse"
    assert profile["endpoint_policy"] == "exclude"
    assert profile["overflow_strategy"] == "deterministic_bucket"
    assert profile["max_depth"] == 8
    assert profile["max_children_per_view"] == 200
    assert profile["max_collection_span_seconds"] == 3600
    assert profile["max_observation_age_seconds"] == 3600
    assert {
        item["projection_kind"] for item in profile["explicit_asset_exceptions"]
    } == {"service_boundary"}


def test_observation_and_parser_references_form_one_closed_set() -> None:
    observation_registry = contracts.load_registry(OBSERVATION_URI)
    parser_registry = contracts.load_registry(PARSER_URI)
    registered = {item["kind"] for item in observation_registry["kinds"]}
    parsed = {
        kind
        for parser in parser_registry["deterministic_parsers"]
        for kind in parser["observation_kinds"]
    }

    assert parsed == registered
    for item in observation_registry["kinds"]:
        assert contracts.load_schema(item["schema_uri"])
    policy = parser_registry["llm_policy"]
    assert policy["tools_allowed"] is False
    assert policy["network_allowed"] is False
    assert contracts.load_schema(policy["candidate_output_schema"])
    assert contracts.load_schema(policy["chunk_input_schema"])
    assert contracts.load_schema(policy["formal_facts_schema"])


@pytest.mark.parametrize("field", ["schema_version", "bundle_id", "redaction_version"])
def test_parser_bundle_rejects_missing_or_changed_identity(field: str) -> None:
    missing = deepcopy(contracts.load_registry(PARSER_URI))
    missing.pop(field)
    changed = deepcopy(contracts.load_registry(PARSER_URI))
    changed[field] = "unsupported/v2"
    kinds = {item["kind"] for item in contracts.load_registry(OBSERVATION_URI)["kinds"]}

    with pytest.raises(contracts.ContractError):
        contracts._validate_parser_registry(missing, kinds)
    with pytest.raises(contracts.ContractError):
        contracts._validate_parser_registry(changed, kinds)


@pytest.mark.parametrize("field", sorted(EXPECTED_POLICY))
def test_parser_bundle_rejects_each_missing_policy_field(field: str) -> None:
    registry = deepcopy(contracts.load_registry(PARSER_URI))
    registry["llm_policy"].pop(field)
    kinds = {item["kind"] for item in contracts.load_registry(OBSERVATION_URI)["kinds"]}

    with pytest.raises(contracts.ContractError, match="frozen contract"):
        contracts._validate_parser_registry(registry, kinds)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_policy_id", "unsupported-prompt/v2"),
        ("model_selection", "caller_selected"),
        ("tools_allowed", True),
        ("network_allowed", True),
        ("candidate_output_schema", "aioe.network-topology.facts/v1"),
        ("chunk_input_schema", "aioe.network-topology.profile/v1"),
        ("formal_facts_schema", "aioe.network-topology.graph/v2"),
    ],
)
def test_parser_bundle_rejects_malformed_or_substituted_policy(
    field: str, value: object
) -> None:
    registry = deepcopy(contracts.load_registry(PARSER_URI))
    registry["llm_policy"][field] = value
    kinds = {item["kind"] for item in contracts.load_registry(OBSERVATION_URI)["kinds"]}

    with pytest.raises(contracts.ContractError, match="frozen contract"):
        contracts._validate_parser_registry(registry, kinds)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parser_version", 0),
        ("parser_version", True),
        ("parser_version", "1"),
        ("parser_id", "   "),
        ("implementation_status", "implemented"),
    ],
)
def test_parser_bundle_rejects_invalid_parser_metadata(
    field: str, value: object
) -> None:
    registry = deepcopy(contracts.load_registry(PARSER_URI))
    registry["deterministic_parsers"][0][field] = value
    kinds = {item["kind"] for item in contracts.load_registry(OBSERVATION_URI)["kinds"]}

    with pytest.raises(contracts.ContractError):
        contracts._validate_parser_registry(registry, kinds)


def test_capabilities_have_full_match_keys_and_required_base_kinds() -> None:
    profiles = contracts.load_registry(CAPABILITY_URI)["profiles"]

    for profile in profiles:
        assert set(profile["match"]) == {
            "device_type",
            "vendor",
            "platform",
            "os_family",
        }
        if profile["id"] == "aioe-action-collection/v1":
            assert profile["required_kinds"] == ["raw.action_output"]
        else:
            assert BASE_REQUIRED <= set(profile["required_kinds"])
        assert not set(profile["required_kinds"]) & set(profile["optional_kinds"])


@pytest.mark.parametrize(
    ("device_type", "vendor", "expected_id"),
    [
        ("switch", "Huawei", "aioe.huawei.switch.generic/v1"),
        ("switch", "H3C", "aioe.h3c.switch.generic/v1"),
        ("switch", "Cisco", "aioe.cisco.switch.generic/v1"),
        ("firewall", "Fortinet", "aioe.fortinet.firewall.generic/v1"),
        ("load_balancer", "F5", "aioe.f5.load_balancer.generic/v1"),
    ],
)
def test_capability_resolution_is_unique_and_normalized(
    device_type: str, vendor: str, expected_id: str
) -> None:
    profile = contracts.resolve_capability(
        device_type, vendor, "audited-platform", "audited-os-family"
    )

    assert profile["id"] == expected_id


def test_fixture_only_capability_is_registered_but_not_runtime_resolvable() -> None:
    registry = contracts.load_registry(CAPABILITY_URI)
    profile = next(
        item
        for item in registry["profiles"]
        if item["id"] == "aioe.synthetic.switch.deletion/v1"
    )

    assert profile["fixture_only"] is True
    assert profile["match"] == {
        "device_type": "switch",
        "vendor": "synthetic",
        "platform": "contract-neutral",
        "os_family": "synthetic-os",
    }
    with pytest.raises(contracts.ContractError, match="No capability profile"):
        contracts.resolve_capability(
            "switch", "synthetic", "contract-neutral", "synthetic-os"
        )


@pytest.mark.parametrize("invalid", [None, {}, [], "", "   "])
@pytest.mark.parametrize("position", range(4))
def test_capability_resolution_rejects_malformed_match_fields(
    invalid: object, position: int
) -> None:
    values: list[object] = ["switch", "huawei", "platform", "os-family"]
    values[position] = invalid

    with pytest.raises(contracts.ContractError, match="non-empty strings"):
        contracts.resolve_capability(*values)


def test_capability_resolution_trims_and_normalizes_all_fields() -> None:
    profile = contracts.resolve_capability(
        " SWITCH ", " HUAWEI ", " platform ", " os-family "
    )

    assert profile["id"] == "aioe.huawei.switch.generic/v1"


def test_capability_resolution_fails_for_missing_or_ambiguous_match() -> None:
    with pytest.raises(contracts.ContractError, match="No capability profile"):
        contracts.resolve_capability("load_balancer", "unknown", "unknown", "unknown")

    profiles = deepcopy(contracts.load_registry(CAPABILITY_URI)["profiles"][:1])
    duplicate = deepcopy(profiles[0])
    duplicate["id"] = "duplicate-profile/v1"
    with pytest.raises(contracts.ContractError, match="ambiguous"):
        contracts._select_capability(
            [profiles[0], duplicate],
            ("switch", "huawei", "platform", "os"),
        )


def test_vendor_mappings_are_planned_and_fail_closed_until_delivery() -> None:
    registry = contracts.load_registry(ACTION_URI)

    assert registry["audited_source"] == {
        "repository": "../aioe_apps",
        "revision": "4e1193792428e888a94c94af9c196ed2d65c009e",
        "semantic_versions_declared": False,
        "observation_v2_compatible_actions": 0,
    }
    runtime_mappings = [
        item for item in registry["mappings"] if item["delivery_status"] == "planned"
    ]
    assert len(runtime_mappings) == 6
    for mapping in runtime_mappings:
        expected_action = (
            "aioe_action_collection"
            if mapping["capability_profile_id"] == "aioe-action-collection/v1"
            else "collect_topology_observations_v2"
        )
        assert mapping["delivery_status"] == "planned"
        assert mapping["planned_minimum_package_version"] == "1.0.0"
        assert mapping["verified_current_package_version"] is None
        assert mapping["planned_action_id"] == expected_action
        assert set(mapping["kind_action_map"].values()) == {expected_action}
        assert all(
            item["classification"] == "connector_material_only"
            for item in mapping["connector_evidence"]
        )
        kind = next(iter(mapping["kind_action_map"]))
        with pytest.raises(contracts.ContractError, match="not delivered"):
            contracts.resolve_vendor_action(
                mapping["capability_profile_id"], kind, require_ready=True
            )
        planned = contracts.resolve_vendor_action(
            mapping["capability_profile_id"], kind, require_ready=False
        )
        assert planned["observation_kind"] == kind
        assert planned["action_id"] == expected_action


def test_fixture_only_action_mapping_cannot_claim_runtime_delivery() -> None:
    registry = contracts.load_registry(ACTION_URI)
    mapping = next(
        item
        for item in registry["mappings"]
        if item["delivery_status"] == "contract_only"
    )

    assert mapping["capability_profile_id"] == "aioe.synthetic.switch.deletion/v1"
    assert mapping["target_package"] == "fixture-only:no-runtime-package"
    assert mapping["verified_current_package_version"] is None
    assert mapping["connector_evidence"] == []
    kind = next(iter(mapping["kind_action_map"]))
    with pytest.raises(contracts.ContractError, match="not delivered"):
        contracts.resolve_vendor_action(mapping["capability_profile_id"], kind)
    resolved = contracts.resolve_vendor_action(
        mapping["capability_profile_id"], kind, require_ready=False
    )
    assert resolved["delivery_status"] == "contract_only"
    assert resolved["action_id"] == "fixture_contract_only"


def test_fixture_only_mapping_rejects_runtime_delivery_claim() -> None:
    registry = deepcopy(contracts.load_registry(ACTION_URI))
    mapping = next(
        item
        for item in registry["mappings"]
        if item["delivery_status"] == "contract_only"
    )
    mapping["delivery_status"] = "ready"
    mapping["verified_current_package_version"] = "1.0.0"
    profiles, kinds = action_validation_inputs()

    with pytest.raises(contracts.ContractError, match="remain contract-only"):
        contracts._validate_action_registry(registry, profiles, kinds)


def test_public_loaders_and_resolvers_return_defensive_copies() -> None:
    schema = contracts.load_schema("aioe.network-topology.profile/v1")
    schema["$id"] = "mutated"
    assert contracts.load_schema("aioe.network-topology.profile/v1")["$id"] != "mutated"

    registry = contracts.load_registry(CAPABILITY_URI)
    registry["profiles"][0]["required_kinds"].clear()
    assert contracts.load_registry(CAPABILITY_URI)["profiles"][0]["required_kinds"]

    profile = contracts.resolve_capability("switch", "huawei", "p", "os")
    profile["id"] = "mutated"
    assert (
        contracts.resolve_capability("switch", "huawei", "p", "os")["id"] != "mutated"
    )

    mapping = contracts.resolve_vendor_action(
        "aioe.huawei.switch.generic/v1",
        "system.identity",
        require_ready=False,
    )
    mapping["kind_action_map"].clear()
    reloaded = contracts.resolve_vendor_action(
        "aioe.huawei.switch.generic/v1",
        "system.identity",
        require_ready=False,
    )
    assert reloaded["kind_action_map"]

    summary = contracts.validate_registry_integrity()
    summary["capability_count"] = 999
    assert contracts.validate_registry_integrity()["capability_count"] == 7


@pytest.mark.parametrize(
    "version",
    [None, "", "1", "1.0", "01.0.0", "1.00.0", "1.0.00", "1.0.0-alpha"],
)
def test_action_mapping_rejects_invalid_planned_semver(version: object) -> None:
    registry = deepcopy(contracts.load_registry(ACTION_URI))
    registry["mappings"][0]["planned_minimum_package_version"] = version
    profiles, kinds = action_validation_inputs()

    with pytest.raises(contracts.ContractError, match="numeric SemVer"):
        contracts._validate_action_registry(registry, profiles, kinds)


@pytest.mark.parametrize("field", ["target_package", "planned_action_id"])
def test_action_mapping_rejects_missing_target_identifiers(field: str) -> None:
    registry = deepcopy(contracts.load_registry(ACTION_URI))
    mapping = registry["mappings"][0]
    mapping[field] = "   "
    if field == "planned_action_id":
        mapping["kind_action_map"] = {
            kind: "   " for kind in mapping["kind_action_map"]
        }
    profiles, kinds = action_validation_inputs()

    with pytest.raises(contracts.ContractError, match="non-empty"):
        contracts._validate_action_registry(registry, profiles, kinds)


@pytest.mark.parametrize("version", [None, "not-semver", "0.9.9"])
def test_ready_action_rejects_invalid_or_below_minimum_version(
    version: object,
) -> None:
    registry = deepcopy(contracts.load_registry(ACTION_URI))
    mapping = registry["mappings"][0]
    mapping["delivery_status"] = "ready"
    mapping["verified_current_package_version"] = version
    profiles, kinds = action_validation_inputs()

    with pytest.raises(contracts.ContractError):
        contracts._validate_action_registry(registry, profiles, kinds)


@pytest.mark.parametrize("version", ["1.0.0", "1.0.1", "2.0.0"])
def test_ready_action_accepts_minimum_or_newer_version(version: str) -> None:
    registry = deepcopy(contracts.load_registry(ACTION_URI))
    mapping = registry["mappings"][0]
    mapping["delivery_status"] = "ready"
    mapping["verified_current_package_version"] = version
    profiles, kinds = action_validation_inputs()

    contracts._validate_action_registry(registry, profiles, kinds)


def test_integrity_helpers_reject_dangling_references() -> None:
    kinds = {item["kind"] for item in contracts.load_registry(OBSERVATION_URI)["kinds"]}
    parser_registry = deepcopy(contracts.load_registry(PARSER_URI))
    parser_registry["deterministic_parsers"][0]["observation_kinds"].pop()

    with pytest.raises(contracts.ContractError, match="exactly one parser"):
        contracts._validate_parser_registry(parser_registry, kinds)

    capability_registry = deepcopy(contracts.load_registry(CAPABILITY_URI))
    capability_registry["profiles"][0]["required_kinds"].remove("system.identity")
    with pytest.raises(contracts.ContractError, match="Identity/interfaces"):
        contracts._validate_capability_registry(capability_registry, kinds)
