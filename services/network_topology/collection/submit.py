"""Submit a collected snapshot through direct or chunked topology ingress."""
from __future__ import annotations

import copy
import inspect
from typing import Any

import orjson
import settings

from services.network_topology import snapshot as topology_snapshot
from services.network_topology import upload as topology_upload
from services.network_topology.canonicalization import CanonicalizationError, canonicalize_and_hash_snapshot, canonicalize_json
from services.network_topology.collection import runs
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.snapshot_builder import PLAYBOOK_ID
from services.network_topology.upload import UploadAuthority, UploadOwner


def _save_run(run):
    saver = getattr(run, "save", None)
    if saver is None:
        return None
    return saver()

async def _persist(run):
    result = _save_run(run)
    if inspect.isawaitable(result):
        await result


def _without_observations(snapshot: dict[str, Any]) -> dict[str, Any]:
    envelope = copy.deepcopy(snapshot)
    for source in envelope.get("sources", []):
        for resource in source.get("resources", []):
            result = resource.get("result") or {}
            observations = result.pop("observations", [])
            result["expected_observation_count"] = len(observations)
            resource["result"] = result
    return envelope


def _observations(snapshot: dict[str, Any]):
    for source in snapshot.get("sources", []):
        for resource in source.get("resources", []):
            for observation in (resource.get("result") or {}).get("observations", []):
                yield resource["resource_id"], observation


async def submit_snapshot(plan, run, *, ingest=None, now=None) -> dict[str, Any]:
    if not getattr(run, "snapshot_draft", None):
        raise CollectionError("COLLECTION_SNAPSHOT_MISSING", 409)
    try:
        snapshot = orjson.loads(run.snapshot_draft)
        body = canonicalize_json(snapshot)
        actor = f"collection-run:{run.id}"
        direct_limit = getattr(settings, "TOPOLOGY_V2_DIRECT_MAX_BYTES", topology_snapshot.DEFAULT_DIRECT_MAX_BYTES)
        if len(body) <= direct_limit:
            accepted = await (ingest or topology_snapshot.ingest_direct_snapshot)(
                run.tenant_id, plan.topology_id, actor, snapshot, body_size=len(body), now=now
            )
        else:
            canonical = canonicalize_and_hash_snapshot(snapshot)
            target_manifest = snapshot.get("target_manifest") or {}
            authority = UploadAuthority(run.tenant_id, plan.topology_id, actor, PLAYBOOK_ID, str(run.id))
            envelope = _without_observations(snapshot)
            started = await topology_upload.start_upload(authority, {
                "id": plan.topology_id,
                "manifest_id": target_manifest.get("manifest_id"),
                "snapshot_envelope_without_observations": envelope,
                "client_expected_input_hash": canonical.payload_hash,
            })
            session_id = started["upload_session_id"]
            owner = UploadOwner(run.tenant_id, actor)
            for resource_id, observation in _observations(snapshot):
                await topology_upload.stage_chunk(owner, {
                    "upload_session_id": session_id,
                    "resource_id": resource_id,
                    "observation": observation,
                })
            accepted = await topology_upload.commit_upload_session(
                owner, session_id, expected_payload_hash=canonical.payload_hash
            )
        run.sync_id = run.analysis_run_id = accepted.sync_id
        run.snapshot_id = accepted.snapshot_id
        run.state = "SUBMITTED"
        run.snapshot_draft = None
        await _persist(run)
        return accepted.as_dict() if hasattr(accepted, "as_dict") else {
            "sync_id": accepted.sync_id, "snapshot_id": accepted.snapshot_id,
            "status": getattr(accepted, "status", "RECEIVED"),
        }
    except topology_snapshot.SnapshotError as error:
        await runs.finish_run(run, "FAILED", error_code=f"SNAPSHOT_{error.code}")
        raise CollectionError(error.code, error.status) from error
    except topology_upload.UploadError as error:
        await runs.finish_run(run, "FAILED", error_code=f"UPLOAD_{error.code}")
        raise CollectionError(error.code, error.status) from error
    except CollectionError as error:
        await runs.finish_run(run, "FAILED", error_code=error.code)
        raise
    except (orjson.JSONDecodeError, CanonicalizationError, TypeError, ValueError) as error:
        await runs.finish_run(run, "FAILED", error_code="SNAPSHOT_INVALID")
        raise CollectionError("SNAPSHOT_INVALID", 422) from error


async def status(plan, run) -> dict[str, Any]:
    await runs.refresh_from_analysis(run)
    return {
        "run_state": getattr(run, "state", None),
        "analysis_state": getattr(run, "analysis_state", None),
        "stage": getattr(run, "stage", None),
        "graph_revision": getattr(run, "_analysis_graph_revision", None) or getattr(run, "graph_revision", None),
        "result": getattr(run, "result", None),
        "error_code": getattr(run, "_analysis_error_code", getattr(run, "error_code", None)),
        "error_message": getattr(run, "_analysis_error_message", getattr(run, "error_message", None)),
        "terminal": getattr(run, "state", None) in {"SUCCEEDED", "FAILED"},
        "device_total": getattr(run, "device_total", 0),
        "device_succeeded": getattr(run, "device_succeeded", 0),
        "item_results": getattr(run, "item_results", None),
    }
