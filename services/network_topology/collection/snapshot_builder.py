# services/network_topology/collection/snapshot_builder.py
"""Assemble a collection/v2 full snapshot from per-item action outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.observations import raw_observation, typed_observations

ACTION_ID = "aioe.network-topology.action-collection"
PLAYBOOK_ID = "aioe-builtin-network-topology-collection"


@dataclass(frozen=True)
class ItemOutcome:
    item_id: str
    app_name: str
    action_func: str
    app_version: str
    resource_id: str
    status: str
    output: Any
    observed_at: str
    error_code: str | None
    error_message: str | None
    duration_ms: int


def _text(value: Any, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return text or default


def asset_context_for(
    resource_name: str, host: str | None, app_name: str, asset: dict | None, resource_id: str,
) -> dict[str, Any]:
    if asset:
        attributes = asset.get("attributes") or {}
        ips = [ip for ip in {attributes.get("ip"), attributes.get("management_ip"), host} if ip]
        importance = attributes.get("importance") if attributes.get("importance") in {"critical", "important"} else "normal"
        scope_ids = [asset["project_area_id"]] if asset.get("project_area_id") else []
        return {
            "name": _text(asset.get("name"), resource_name), "management_ips": sorted(ips),
            "management_domain_id": "default", "vendor": _text(attributes.get("vendor") or attributes.get("brand"), app_name),
            "model": _text(attributes.get("model")), "platform": _text(attributes.get("platform")),
            "os_family": _text(attributes.get("os_family")), "os_version": _text(attributes.get("os_version")),
            "device_type": _text(attributes.get("device_type") or asset.get("asset_model_key")),
            "role": _text(attributes.get("role")), "importance": importance, "scope_ids": scope_ids,
            "tags": sorted({str(t) for t in attributes.get("tags") or []}),
            "provenance_ref": f"asset:{asset['id']}",
        }
    return {
        "name": _text(resource_name), "management_ips": [host] if host else [],
        "management_domain_id": "default", "vendor": _text(app_name), "model": "unknown",
        "platform": "unknown", "os_family": "unknown", "os_version": "unknown",
        "device_type": "unknown", "role": "unknown", "importance": "normal",
        "scope_ids": [], "tags": [], "provenance_ref": f"resource:{resource_id}",
    }


def build_snapshot(
    *, collection_run_id: str, manifest: dict, profile_revision: int,
    outcomes: list[ItemOutcome], asset_contexts: dict[str, dict],
    started_at: str, ended_at: str,
) -> dict:
    """Merge item observations by ToolResources.id; never emit a partial snapshot."""
    failed = [o for o in outcomes if o.status != "success"]
    if failed or not outcomes:
        raise CollectionError("COLLECTION_INCOMPLETE")
    by_resource: dict[str, list[dict[str, Any]]] = {}
    for outcome in outcomes:
        observations = by_resource.setdefault(outcome.resource_id, [])
        observations.append(raw_observation(
            observation_id=f"{outcome.item_id}:{outcome.resource_id}", app_name=outcome.app_name,
            action_func=outcome.action_func, app_version=outcome.app_version,
            resource_id=outcome.resource_id, output=outcome.output, observed_at=outcome.observed_at,
        ))
        observations.extend(typed_observations(outcome.output, resource_id=outcome.resource_id,
                                               item_id=outcome.item_id, observed_at=outcome.observed_at))
    expected_ids = sorted(item["resource_id"] for item in manifest["expected_resources"])
    if sorted(by_resource) != expected_ids or not set(expected_ids) <= asset_contexts.keys():
        raise CollectionError("COLLECTION_INCOMPLETE")
    for expected in manifest["expected_resources"]:
        observed_kinds = {item["kind"] for item in by_resource[expected["resource_id"]]}
        if not set(expected["required_kinds"]) <= observed_kinds:
            raise CollectionError("COLLECTION_INCOMPLETE")
    resources = [
        {"resource_id": rid, "asset_context": asset_contexts[rid],
         "result": {"status": "success", "observations": by_resource[rid]}}
        for rid in expected_ids
    ]
    coverage = [
        {"resource_id": rid,
         "collected_kinds": sorted({o["kind"] for o in by_resource[rid]}),
         "kind_status": {kind: "success" for kind in sorted({o["kind"] for o in by_resource[rid]})}}
        for rid in expected_ids
    ]
    return {
        "schema_version": "aioe.network-topology.collection/v2",
        "snapshot_id": f"collection-{collection_run_id}",
        "collection_started_at": started_at,
        "collection_ended_at": ended_at,
        "collector": {"playbook_id": PLAYBOOK_ID, "playbook_version": 1,
                      "run_id": collection_run_id, "collector_version": "1.0.0"},
        "topology_profile_revision": profile_revision,
        "target_manifest": manifest,
        "completeness": {"mode": "full", "asserted": True, "expected_resource_ids": expected_ids,
                         "collected_resource_ids": expected_ids, "failed_resource_ids": [],
                         "observation_coverage": coverage},
        "authoritative_context": {"scopes": [], "subnets": [], "external_networks": [],
                                  "circuits": [], "cloud_networks": []},
        "sources": [{"action_id": ACTION_ID, "action_version": "1.0.0", "resources": resources}],
    }
