from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.network_topology.identity import (
    IdentityRegistry,
    IdentityRecord,
    IdentityResolution,
)
from services.network_topology.contracts import validate_contract
from services.network_topology.importance import assess_importance
from services.network_topology.scope_tree import (
    ScopeTreeError,
    build_scope_tree,
    lowest_common_ancestor,
    network_segment_id,
)


ROOT = "scope:organization:root"


def _profile(**overrides):
    profile = {
        "root_scope_seed": ROOT,
        "grouping_order": [
            "organization",
            "region",
            "site",
            "network_domain",
            "security_zone",
            "vrf",
        ],
        "key_device_roles": [
            "firewall",
            "router",
            "core_switch",
            "distribution_switch",
            "load_balancer",
            "vpn_gateway",
            "sdwan_gateway",
            "internet_gateway",
        ],
        "excluded_asset_types": ["host", "terminal"],
        "important_tags": ["topology.important"],
        "explicit_asset_exceptions": [
            {
                "asset_type": "host",
                "projection_kind": "service_boundary",
                "allowed_network_attributes": ["network_prefixes", "ports"],
            }
        ],
        "access_device_policy": "collapse",
        "endpoint_policy": "exclude",
        "max_depth": 8,
    }
    profile.update(overrides)
    return profile


def _device(identifier, *, device_type="firewall", roles=("firewall",), **attrs):
    return {
        "id": f"device:{identifier}",
        "attributes": {
            "device_type": device_type,
            "roles": list(roles),
            "inventory_state": "managed",
            **attrs,
        },
        "provenance_ids": [f"p:{identifier}"],
    }


def _facts(*devices, segments=(), gateways=(), routes=(), boundaries=()):
    return {
        "schema_version": "aioe.network-topology.facts/v1",
        "snapshot_id": "snapshot-1",
        "devices": list(devices),
        "device_groups": [],
        "segments": list(segments),
        "gateways": list(gateways),
        "routes": list(routes),
        "routing_relationships": [],
        "security_boundaries": list(boundaries),
        "provenance_catalog": [
            {
                "id": "p:authority",
                "provenance": {
                    "type": "authoritative",
                    "source_system": "cmdb",
                    "source_record_id": "asset-1",
                    "source_revision": "7",
                    "as_of": "2026-07-30T00:00:00Z",
                    "field_locator": "/asset",
                },
            }
        ],
    }


def _resolution(*fact_ids):
    mapping = {fact_id: f"canonical:{fact_id}" for fact_id in fact_ids}
    identities = tuple(
        IdentityRecord("tenant-a", canonical_id, "device", f"hash:{fact_id}")
        for fact_id, canonical_id in mapping.items()
    )
    return IdentityResolution(identities, (), (), (), mapping)


def _scope(code, scope_type, *, parent_code=None, name=None):
    value = {
        "code": code,
        "scope_type": scope_type,
        "name": name or code,
        "provenance_ids": ["p:authority"],
    }
    if parent_code is not None:
        value["parent_code"] = parent_code
    return value


def test_missing_profile_layers_can_jump_but_depth_and_cycles_fail():
    facts = _facts(_device("fw"))
    scopes = [
        _scope("org", "organization"),
        _scope("site-1", "site", parent_code="org"),
        _scope("blue", "vrf", parent_code="site-1"),
    ]
    result = build_scope_tree(
        facts,
        _resolution("device:fw"),
        _profile(max_depth=3),
        authoritative_scopes=scopes,
        entity_memberships={"device:fw": ["blue"]},
    )

    by_id = {item["id"]: item for item in result.scopes}
    assert by_id["scope:network_context:blue"]["parent_scope_id"] == "scope:site:site-1"

    cyclic = [replace_scope(scopes[0], parent_code="blue"), *scopes[1:]]
    with pytest.raises(ScopeTreeError, match="cycle"):
        build_scope_tree(
            facts,
            _resolution("device:fw"),
            _profile(max_depth=3),
            authoritative_scopes=cyclic,
        )


def replace_scope(scope, **changes):
    return {**scope, **changes}


def test_multi_membership_firewall_primary_scope_is_lca_and_stored_once():
    facts = _facts(_device("fw"))
    scopes = [
        _scope("site-1", "site"),
        _scope("corp", "vrf", parent_code="site-1"),
        _scope("trust", "security_zone", parent_code="site-1"),
    ]
    result = build_scope_tree(
        facts,
        _resolution("device:fw"),
        _profile(),
        authoritative_scopes=scopes,
        entity_memberships={"device:fw": ["corp", "trust"]},
    )

    assert len(result.entities) == 1
    entity = result.entities[0]
    assert entity["scope_id"] == "scope:site:site-1"
    assert entity["scope_memberships"] == [
        "scope:network_context:corp",
        "scope:security_zone:trust",
    ]
    parents = {item["id"]: item["parent_scope_id"] for item in result.scopes}
    assert lowest_common_ancestor(entity["scope_memberships"], parents) == entity["scope_id"]


def test_conflicting_authority_is_unresolved_and_uses_unassigned_scope():
    facts = _facts(_device("fw"))
    scopes = [_scope("site-a", "site"), _scope("site-b", "site")]
    result = build_scope_tree(
        facts,
        _resolution("device:fw"),
        _profile(),
        authoritative_scopes=scopes,
        entity_memberships={"device:fw": ["site-a", "site-b"]},
    )

    entity = result.entities[0]
    assert entity["scope_id"] == "scope:custom:unassigned"
    assert entity["scope_memberships"] == ["scope:custom:unassigned"]
    assert result.unresolved[0].reason_code == "AUTHORITATIVE_SCOPE_CONFLICT"
    assert any(item["id"] == "scope:custom:unassigned" for item in result.scopes)


def test_missing_or_unknown_membership_uses_deterministic_unassigned_scope():
    result = build_scope_tree(
        _facts(_device("fw")),
        _resolution("device:fw"),
        _profile(),
        authoritative_scopes=[_scope("site-a", "site")],
        entity_memberships={"device:fw": ["missing-site"]},
    )

    assert result.entities[0]["scope_id"] == "scope:custom:unassigned"
    assert result.unresolved[0].reason_code == "UNKNOWN_SCOPE_MEMBERSHIP"


def test_absent_membership_with_business_scopes_is_unassigned():
    result = build_scope_tree(
        _facts(_device("fw")),
        _resolution("device:fw"),
        _profile(),
        authoritative_scopes=[_scope("site-a", "site")],
    )

    assert result.entities[0]["scope_id"] == "scope:custom:unassigned"
    assert result.unresolved[0].reason_code == "MISSING_SCOPE_MEMBERSHIP"


def test_network_segment_identity_includes_domain_vrf_and_canonical_cidr():
    left = network_segment_id("domain-a", "blue", "198.51.100.65/26")
    right = network_segment_id("domain-a", "red", "198.51.100.64/26")
    other_domain = network_segment_id("domain-b", "blue", "198.51.100.64/26")

    assert "198.51.100.64-26" in left
    assert len({left, right, other_domain}) == 3


def test_importance_reasons_are_nonempty_and_trace_fact_provenance():
    device = _device("edge", roles=("router",), important=True)
    facts = _facts(
        device,
        gateways=({"id": "gateway:1", "attributes": {"gateway_id": device["id"]}, "provenance_ids": ["p:gw"]},),
        routes=({"id": "route:1", "attributes": {"device_id": device["id"], "route_type": "default"}, "provenance_ids": ["p:route"]},),
        boundaries=({"id": "security:1", "attributes": {"device_id": device["id"]}, "provenance_ids": ["p:boundary"]},),
    )
    decision = assess_importance(device, facts, _profile())

    assert decision.level == "critical"
    assert decision.reasons
    assert {reason.code for reason in decision.reasons} >= {
        "authoritative_asset_mark",
        "asset_role",
        "gateway_fact",
        "boundary_fact",
        "routing_fact",
    }
    assert {item for reason in decision.reasons for item in reason.fact_ids} >= {
        "device:edge",
        "gateway:1",
        "route:1",
        "security:1",
    }


def test_host_exception_is_minimal_and_access_switch_is_collapsed():
    host = _device(
        "service",
        device_type="host",
        roles=(),
        important=True,
        network_prefixes=["192.0.2.10/32"],
        ports=[443],
        vendor="must-not-leak",
    )
    access = _device("access", device_type="switch", roles=("access_switch",))
    result = build_scope_tree(
        _facts(host, access),
        _resolution("device:service", "device:access"),
        _profile(),
    )

    assert len(result.entities) == 1
    boundary = result.entities[0]
    assert boundary["id"] == "canonical:device:service"
    assert boundary["kind"] == "service_boundary"
    assert boundary["attributes"] == {
        "boundary_type": "host",
        "network_prefixes": ["192.0.2.10/32"],
        "ports": [443],
    }
    assert boundary["importance_reasons"] == ["authoritative_profile_exception"]
    assert result.collapsed_fact_ids == ("device:access",)


def test_evidence_backed_access_switch_is_not_collapsed():
    access = _device("access", device_type="switch", roles=("access_switch",))
    gateway = {
        "id": "gateway:access",
        "attributes": {"gateway_id": access["id"]},
        "provenance_ids": ["p:gateway"],
    }
    result = build_scope_tree(
        _facts(access, gateways=(gateway,)),
        _resolution("device:access"),
        _profile(),
    )

    assert result.collapsed_fact_ids == ()
    assert result.entities[0]["importance_reasons"] == ["gateway_fact"]


def test_profile_depth_is_enforced_even_without_a_cycle():
    scopes = [
        _scope("region", "region"),
        _scope("site", "site", parent_code="region"),
        _scope("domain", "network_domain", parent_code="site"),
    ]
    with pytest.raises(ScopeTreeError, match="max_depth"):
        build_scope_tree(
            _facts(),
            _resolution(),
            _profile(max_depth=2),
            authoritative_scopes=scopes,
        )


@pytest.mark.parametrize("vendor", ["huawei", "h3c", "cisco", "fortinet", "adc_lb"])
def test_frozen_golden_facts_produce_contract_valid_scope_entities(vendor):
    fixture = Path("tests/fixtures/network_topology") / vendor / "full-golden-v1.json"
    case = json.loads(fixture.read_text())
    facts = case["expected_facts"]
    expected = case["expected_presentation_graph"]
    resolution = IdentityRegistry("tenant-a", f"fixture-{vendor}").resolve_facts(facts)

    result = build_scope_tree(facts, resolution, _profile())
    graph = result.as_graph()
    validate_contract(graph, "aioe.network-topology.graph/v2")

    assert graph["schema_version"] == "aioe.network-topology.graph/v2"
    assert graph["root_scope_id"] == ROOT
    assert {item["id"] for item in graph["scopes"]} == {
        item["id"] for item in expected["scopes"]
    }
    for entity in graph["entities"]:
        assert set(entity) == {
            "id",
            "scope_id",
            "scope_memberships",
            "kind",
            "name",
            "role",
            "representation",
            "importance",
            "importance_reasons",
            "attributes",
            "provenance",
        }
        assert entity["importance_reasons"]
