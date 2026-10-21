"""Internal job input and candidate submission helpers for the topology agent."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import settings
from models.network_topology_v2 import TopologyAgentJob
from services.action_collection.agent_tokens import authenticate_job
from services.network_topology.contracts import iter_schemas
from services.network_topology.facts.candidate_conversion import (
    CandidateConversionError,
    ConversionResult,
    convert_candidates,
)
from services.network_topology.run_pipeline import build_collection_chunks

AGENT_TIMEOUT_SECONDS = 1800


def schema_bundle() -> dict[str, dict]:
    return {schema["$id"]: schema for schema in iter_schemas()}


def _resources(collection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    resources: dict[str, dict[str, Any]] = {}
    for source in collection.get("sources", []):
        for resource in source.get("resources", []):
            resource_id = resource.get("resource_id")
            if isinstance(resource_id, str):
                resources[resource_id] = resource
    return resources


def build_agent_input(collection: dict, agent_observation_ids: list[str]) -> list[dict]:
    """Build one stable input envelope per resource containing all raw evidence."""
    selected = set(agent_observation_ids)
    resources = _resources(collection)
    chunks = build_collection_chunks(collection)
    by_resource: dict[str, list[Any]] = {}
    for chunk in chunks:
        by_resource.setdefault(chunk.resource_id, []).append(chunk)

    result: list[dict] = []
    for resource_id in sorted(resources):
        observations = [
            item for item in resources[resource_id].get("result", {}).get("observations", [])
            if item.get("observation_status") == "success"
        ]
        if not any(item.get("observation_id") in selected for item in observations):
            continue
        candidates = [c for c in by_resource.get(resource_id, ()) if c.domain == "raw"]
        chunk = candidates[0] if candidates else (by_resource.get(resource_id) or [None])[0]
        if chunk is None:
            continue
        result.append({
            "resource_id": resource_id,
            "chunk_id": chunk.chunk_id,
            "input_hash": chunk.input_hash,
            # Match the registered LLM parser's context allowlist. Display
            # names, addresses and provenance are bound by the server, never
            # supplied to the agent as observation evidence.
            "asset_context": {
                key: deepcopy((resources[resource_id].get("asset_context") or {}).get(key))
                for key in ("vendor", "platform", "os_family", "device_type")
            },
            "observations": deepcopy(observations),
        })
    return result


def convert_documents(collection: dict, run: Any, documents: list[dict]) -> tuple[tuple[ConversionResult, ...], list[dict]]:
    resources = _resources(collection)
    chunks = build_collection_chunks(collection)
    by_key = {(c.resource_id, c.chunk_id): c for c in chunks}
    allowed_ids = getattr(run, "agent_observation_ids", None)
    if allowed_ids is None:
        allowed_ids = [o["observation_id"] for r in resources.values()
                       for o in r.get("result", {}).get("observations", [])]
    bindings = {r["resource_id"]: r["chunk_id"] for r in build_agent_input(collection, allowed_ids)}
    results: list[ConversionResult] = []
    errors: list[dict] = []
    seen = set()
    if not isinstance(documents, list):
        return (), [{"resource_id": None, "message": "documents must be an array"}]
    for item in documents:
        resource_id = item.get("resource_id") if isinstance(item, dict) else None
        chunk_id = item.get("chunk_id") if isinstance(item, dict) else None
        if not isinstance(resource_id, str) or resource_id not in bindings:
            errors.append({"resource_id": resource_id, "message": "resource is outside this job"})
            continue
        chunk = by_key.get((resource_id, chunk_id)) if isinstance(chunk_id, str) else None
        if chunk is None or bindings[resource_id] != chunk_id or resource_id in seen:
            errors.append({"resource_id": resource_id, "message": "chunk is outside this job"})
            continue
        seen.add(resource_id)
        try:
            result = convert_candidates(
                item.get("document"), snapshot_id=collection["snapshot_id"],
                analysis_run_id=str(run.id), resource_id=resource_id,
                chunk_id=chunk_id, input_hash=chunk.input_hash,
                observations=tuple(resources[resource_id].get("result", {}).get("observations", [])),
            )
            # The canonical device is the server-bound collection resource,
            # just as in structured system.identity parsing. Its inventory
            # identity must not depend on an agent guess or a serial number.
            for device in result.facts["devices"]:
                if device["id"] == f"device:{resource_id}":
                    device["attributes"]["inventory_state"] = "managed"
            results.append(result)
        except CandidateConversionError as exc:
            errors.append({"resource_id": resource_id, "message": str(exc)})
    return tuple(results), errors


async def authenticate(request_headers: Any, body: dict) -> TopologyAgentJob:
    """Authenticate an internal caller and bind its one-time job token."""
    # Resolve the model per call: tests replace agent_jobs.TopologyAgentJob.
    return await authenticate_job(request_headers, body, job_model=TopologyAgentJob,
                                  timeout_seconds=AGENT_TIMEOUT_SECONDS)
