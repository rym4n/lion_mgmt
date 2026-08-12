from __future__ import annotations

import pytest

from services.network_topology.identity import (
    IdentityRegistry,
    IdentityTransitionError,
    normalize_alias,
)


def _device(
    identifier: str,
    *,
    resource_id: str | None = None,
    inventory_state: str = "managed",
    vendor: str = "huawei",
    aliases: tuple[dict[str, str], ...] = (),
    management_ips: tuple[str, ...] = (),
) -> dict:
    return {
        "id": f"device:{identifier}",
        "fact_family": "device",
        "resource_ids": [resource_id or identifier],
        "source_candidate_ids": [],
        "attributes": {
            "device_type": "switch",
            "identity_aliases": list(aliases),
            "roles": [],
            "inventory_state": inventory_state,
            "vendor": vendor,
            "management_ips": list(management_ips),
        },
        "provenance_ids": [f"p:{identifier}"],
        "parsing_state": "verified",
    }


def _facts(*devices: dict, groups: tuple[dict, ...] = ()) -> dict:
    return {
        "schema_version": "aioe.network-topology.facts/v1",
        "snapshot_id": "snapshot-1",
        "analysis_run_id": "run-1",
        "devices": list(devices),
        "device_groups": list(groups),
    }


def _alias(kind: str, namespace: str, value: str, subtype: str | None = None):
    value_dict = {"type": kind, "namespace": namespace, "value": value}
    if subtype is not None:
        value_dict["subtype"] = subtype
    return value_dict


def test_managed_canonical_id_uses_source_namespace_and_asset_id():
    registry = IdentityRegistry(tenant_id="tenant-a", source_namespace="cmdb")
    result = registry.resolve_facts(_facts(_device("asset-42")))

    assert result.identities[0].canonical_id == "device:cmdb:asset-42"
    assert result.unresolved == ()


def test_overlapping_management_ip_isolated_by_domain_and_vrf():
    left = _alias("management_ip", "domain-a/blue", "192.0.2.10")
    right = _alias("management_ip", "domain-b/blue", "192.0.2.10")
    registry = IdentityRegistry("tenant-a", "cmdb")

    result = registry.resolve_facts(
        _facts(_device("left", aliases=(left,)), _device("right", aliases=(right,)))
    )

    aliases = {alias.namespace for item in result.identities for alias in item.aliases}
    assert aliases == {
        "management-domain=domain-a;vrf=blue",
        "management-domain=domain-b;vrf=blue",
    }
    assert result.unresolved == ()


def test_same_hostname_is_weak_and_does_not_merge_devices():
    hostname = _alias("hostname", "corp.example", "edge-1")
    registry = IdentityRegistry("tenant-a", "cmdb")

    result = registry.resolve_facts(
        _facts(_device("asset-a", aliases=(hostname,)), _device("asset-b", aliases=(hostname,)))
    )

    assert {item.canonical_id for item in result.identities} == {
        "device:cmdb:asset-a",
        "device:cmdb:asset-b",
    }
    assert [item.reason_code for item in result.unresolved] == ["ALIAS_CONFLICT"]


def test_serial_and_chassis_namespaces_include_required_context():
    huawei = normalize_alias(
        _alias("serial", "inventory", "SERIAL-1"), vendor="huawei"
    )
    h3c = normalize_alias(
        _alias("serial", "inventory", "SERIAL-1"), vendor="h3c"
    )
    physical = normalize_alias(
        _alias("chassis", "default", "00:11:22:33:44:55", "mac"),
        vendor="huawei",
    )
    virtual = normalize_alias(
        _alias("chassis", "vsys-1", "00:11:22:33:44:55", "mac"),
        vendor="huawei",
    )

    assert huawei.namespace == "vendor=huawei;source=inventory"
    assert h3c.namespace == "vendor=h3c;source=inventory"
    assert physical.namespace == "vendor=huawei;subtype=mac;context=default"
    assert virtual.namespace == "vendor=huawei;subtype=mac;context=vsys-1"
    assert len({item.key for item in (huawei, h3c, physical, virtual)}) == 4


def test_unmanaged_weak_match_and_alias_conflict_remain_unresolved():
    registry = IdentityRegistry("tenant-a", "discovery")
    hostname = _alias("hostname", "dns", "shared")
    weak = registry.resolve_facts(
        _facts(_device("weak", inventory_state="discovered", aliases=(hostname,)))
    )
    assert weak.identities == ()
    assert weak.unresolved[0].reason_code == "WEAK_IDENTITY_EVIDENCE"

    serial = _alias("serial", "inventory", "SERIAL-9")
    registry.resolve_facts(_facts(_device("a", aliases=(serial,))))
    conflict = registry.resolve_facts(_facts(_device("b", aliases=(serial,))))
    assert conflict.identities[0].canonical_id == "device:discovery:b"
    assert conflict.unresolved[0].reason_code == "ALIAS_CONFLICT"
    assert conflict.audits[0].operation == "alias_conflict"


def test_unmanaged_strong_identity_is_stable_when_alias_is_added():
    registry = IdentityRegistry("tenant-a", "discovery")
    serial = _alias("serial", "inventory", "SERIAL-10")
    first = registry.resolve_facts(
        _facts(_device("unknown-1", inventory_state="discovered", aliases=(serial,)))
    )
    canonical_id = first.identities[0].canonical_id

    second = registry.resolve_facts(
        _facts(
            _device(
                "renamed-candidate",
                inventory_state="discovered",
                aliases=(serial, _alias("hostname", "dns", "new-name")),
            )
        )
    )

    assert second.identities[0].canonical_id == canonical_id
    assert {alias.alias_type for alias in second.identities[0].aliases} == {
        "serial",
        "hostname",
    }


@pytest.mark.parametrize("group_type", ["ha", "stack", "irf", "vss", "mlag"])
def test_logical_device_groups_keep_physical_members_traceable(group_type):
    group = {
        "id": f"device-group:pair-{group_type}",
        "attributes": {
            "group_type": group_type,
            "identity": f"PAIR-{group_type}",
            "member_ids": ["device:a", "device:b"],
        },
        "provenance_ids": ["p:group"],
    }
    registry = IdentityRegistry("tenant-a", "cmdb")

    result = registry.resolve_facts(
        _facts(_device("a"), _device("b"), groups=(group,))
    )

    assert result.groups[0].canonical_id == f"device-group:cmdb:pair-{group_type}"
    assert result.groups[0].physical_member_ids == (
        "device:cmdb:a",
        "device:cmdb:b",
    )


def test_merge_split_replacement_are_audited_and_merge_calls_migrator():
    migrated = []
    registry = IdentityRegistry(
        "tenant-a",
        "cmdb",
        presentation_migrator=lambda source, target, audit: migrated.append(
            (source, target, audit.operation)
        ),
    )
    registry.resolve_facts(_facts(_device("a"), _device("b"), _device("c")))

    merge = registry.record_transition(
        "merge", ("device:cmdb:a", "device:cmdb:b"), ("device:cmdb:c",),
        actor="operator",
    )
    registry.record_transition(
        "replacement", ("device:cmdb:c",), ("device:cmdb:d",), actor="operator"
    )
    registry.record_transition(
        "split", ("device:cmdb:c",), ("device:cmdb:c1", "device:cmdb:c2"),
        actor="operator",
    )

    assert merge.operation == "merge"
    assert migrated == [
        ("device:cmdb:a", "device:cmdb:c", "merge"),
        ("device:cmdb:b", "device:cmdb:c", "merge"),
    ]
    assert {item.operation for item in registry.audits} >= {
        "merge", "replacement", "split"
    }
    with pytest.raises(IdentityTransitionError):
        registry.record_transition(
            "merge", ("device:cmdb:a",),
            ("device:cmdb:b", "device:cmdb:c"), actor="operator",
        )
