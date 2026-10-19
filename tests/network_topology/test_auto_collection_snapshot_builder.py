# tests/network_topology/test_auto_collection_snapshot_builder.py
import pytest

from services.network_topology import contracts
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.snapshot_builder import (
    ItemOutcome, asset_context_for, build_snapshot,
)

MANIFEST = {
    "manifest_id": "m1", "manifest_hash": "sha256:" + "1" * 64, "collection_sequence": 1,
    "base_graph_revision": 0, "base_publication_revision": 0, "profile_revision": 1,
    "issued_at": "2026-10-14T00:00:00Z", "expires_at": "2026-10-14T01:00:00Z",
    "expected_resources": [{"resource_id": "res-1", "capability_profile": "aioe-action-collection/v1",
                            "required_kinds": ["raw.action_output"], "optional_kinds": []}],
}


def _outcome(item_id, status="success"):
    return ItemOutcome(item_id=item_id, app_name="huawei_switch",
                       action_func="collect_topology_observations_v2", app_version="v",
                       resource_id="res-1", status=status, output={"code": 200, "data": {"records": []}},
                       observed_at="2026-10-14T00:10:00Z", error_code=None, error_message=None, duration_ms=5)


def _contexts():
    return {"res-1": asset_context_for("sw1", "10.0.0.1", "huawei_switch", None, "res-1")}


def test_same_resource_two_items_merges_into_one_resource():
    snapshot = build_snapshot(collection_run_id="run-1", manifest=MANIFEST, profile_revision=1,
                              outcomes=[_outcome("item-a"), _outcome("item-b")], asset_contexts=_contexts(),
                              started_at="2026-10-14T00:05:00Z", ended_at="2026-10-14T00:20:00Z")
    resources = snapshot["sources"][0]["resources"]
    assert [r["resource_id"] for r in resources] == ["res-1"]
    assert len(resources[0]["result"]["observations"]) == 2
    contracts.validate_contract(snapshot, "aioe.network-topology.collection/v2")


def test_any_failure_blocks_full_snapshot():
    with pytest.raises(CollectionError) as exc:
        build_snapshot(collection_run_id="run-1", manifest=MANIFEST, profile_revision=1,
                       outcomes=[_outcome("item-a", status="failed")], asset_contexts=_contexts(),
                       started_at="2026-10-14T00:05:00Z", ended_at="2026-10-14T00:20:00Z")
    assert exc.value.code == "COLLECTION_INCOMPLETE"


def test_unlinked_asset_context_defaults():
    ctx = asset_context_for("sw1", "10.0.0.1", "huawei_switch", None, "res-1")
    assert ctx["vendor"] == "huawei_switch"
    assert ctx["management_ips"] == ["10.0.0.1"]
    assert ctx["importance"] == "normal"
    assert ctx["provenance_ref"] == "resource:res-1"


def _build(outcomes, manifest=None, contexts=None):
    return build_snapshot(collection_run_id="run-1", manifest=manifest or MANIFEST,
                          profile_revision=1, outcomes=outcomes,
                          asset_contexts=contexts if contexts is not None else _contexts(),
                          started_at="2026-10-14T00:05:00Z", ended_at="2026-10-14T00:20:00Z")


@pytest.mark.parametrize("case", ["empty", "missing", "unexpected", "missing_context", "missing_kind"])
def test_incomplete_inputs_never_assert_full_collection(case):
    from copy import deepcopy
    from dataclasses import replace
    manifest = deepcopy(MANIFEST)
    outcomes, contexts = [_outcome("a")], _contexts()
    if case == "empty":
        outcomes = []
    elif case == "missing":
        manifest["expected_resources"].append({**manifest["expected_resources"][0], "resource_id": "res-2"})
    elif case == "unexpected":
        outcomes.append(replace(_outcome("b"), resource_id="res-2"))
    elif case == "missing_context":
        contexts = {}
    else:
        manifest["expected_resources"][0]["required_kinds"].append("system.identity")
    with pytest.raises(CollectionError, match="COLLECTION_INCOMPLETE"):
        _build(outcomes, manifest, contexts)


def test_merged_observations_keep_input_order_hashes_and_exact_coverage():
    from dataclasses import replace
    from services.network_topology.canonicalization import validate_observation_hashes
    from services.network_topology.snapshot import _validate_completeness
    output = {"password": "secret", "data": {"records": [
        {"kinds": ["l2.neighbors"], "data": {"neighbors": []}}]}}
    snapshot = _build([replace(_outcome("b"), output=output), _outcome("a")])
    observations = snapshot["sources"][0]["resources"][0]["result"]["observations"]
    assert [o["observation_id"] for o in observations] == ["b:res-1", "b:res-1:0", "a:res-1"]
    assert "password" not in observations[0]["data"]["output"]
    for observation in observations:
        validate_observation_hashes(observation)
    assert snapshot["completeness"]["observation_coverage"] == [{
        "resource_id": "res-1", "collected_kinds": ["l2.neighbors", "raw.action_output"],
        "kind_status": {"l2.neighbors": "success", "raw.action_output": "success"}}]
    contracts.validate_contract(snapshot, "aioe.network-topology.collection/v2")
    _validate_completeness(snapshot)
    assert snapshot["sources"][0]["action_id"] == "aioe.network-topology.action-collection"
    assert snapshot["sources"][0]["action_version"] == "1.0.0"
    assert snapshot["collector"] == {"playbook_id": "aioe-builtin-network-topology-collection",
        "playbook_version": 1, "run_id": "run-1", "collector_version": "1.0.0"}


def test_linked_asset_identity_normalizes_sets_and_defaults():
    asset = {"id": "asset-1", "name": " core ", "project_area_id": "scope-1",
             "asset_model_key": "switch", "attributes": {"ip": "10.0.0.2",
             "management_ip": "10.0.0.1", "brand": "Huawei", "importance": "critical",
             "tags": ["b", "a", "b"]}}
    ctx = asset_context_for("fallback", "10.0.0.1", "app", asset, "res-1")
    assert ctx == {"name": "core", "management_ips": ["10.0.0.1", "10.0.0.2"],
        "management_domain_id": "default", "vendor": "Huawei", "model": "unknown",
        "platform": "unknown", "os_family": "unknown", "os_version": "unknown",
        "device_type": "switch", "role": "unknown", "importance": "critical",
        "scope_ids": ["scope-1"], "tags": ["a", "b"], "provenance_ref": "asset:asset-1"}


def test_raw_action_scopes_survive_snapshot_admission_and_storage():
    from copy import deepcopy
    from dataclasses import replace
    from datetime import datetime, timezone
    from services.network_topology.canonicalization import (
        canonicalize_and_hash_manifest, canonicalize_json, validate_observation_hashes,
    )
    from services.network_topology.payload_store import prepare_stored_envelope
    from services.network_topology.snapshot import validate_direct_snapshot

    manifest = deepcopy(MANIFEST)
    manifest.pop("manifest_hash")
    canonical = canonicalize_and_hash_manifest(manifest)
    manifest = {**canonical.projection["target_manifest"], "manifest_hash": canonical.manifest_hash}
    output = {"code": 200, "data": {"configs": {"routes_ipv4": {"scopes": [
        {"query": {"tableId": 251}, "data": {"totalItems": 0, "items": []}},
        {"query": {"tableId": 250}, "data": {"totalItems": 0, "items": []}},
    ]}}}}
    snapshot = _build([replace(_outcome("a"), output=output)], manifest=manifest)

    admitted = validate_direct_snapshot(
        "topo-1", snapshot, body_size=len(canonicalize_json(snapshot)),
        now=datetime(2026, 10, 14, 0, 20, tzinfo=timezone.utc),
    )
    prepared = prepare_stored_envelope(snapshot)
    stored = prepare_stored_envelope(prepared.normalized)

    assert admitted.payload_hash == prepared.payload_hash == stored.payload_hash
    observation = stored.normalized["sources"][0]["resources"][0]["result"]["observations"][0]
    assert observation["data"]["output"] == output
    validate_observation_hashes(observation)
