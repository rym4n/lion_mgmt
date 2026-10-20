"""Agent job input assembly, submission persistence and summaries."""
from __future__ import annotations

from copy import deepcopy

import orjson

from models.network_access_control import NetworkAccessPolicyCollectionRun
from services.action_collection.errors import CollectionError

AGENT_TIMEOUT_SECONDS = 1800


def observations_for(raw_outputs: dict, resource_id: str) -> list[dict]:
    entries = raw_outputs.get(resource_id) if isinstance(raw_outputs, dict) else None
    if not isinstance(entries, list):
        return []
    return [{
        "observation_id": f"{resource_id}:{entry.get('item_id')}",
        "kind": "raw.action_output",
        "data": {"app_name": entry.get("app_name"), "action_func": entry.get("action_func"),
                 "app_version": entry.get("app_version"), "output": deepcopy(entry.get("output"))},
    } for entry in entries if isinstance(entry, dict)]


def build_agent_input(raw_outputs: dict, frozen_targets: list[dict], resource_ids: list[str]) -> list[dict]:
    names = {str(t.get("resource_id")): t.get("resource_name") for t in frozen_targets or [] if isinstance(t, dict)}
    resources = []
    for resource_id in sorted(set(resource_ids)):
        observations = observations_for(raw_outputs, resource_id)
        if observations:
            resources.append({"resource_id": resource_id, "resource_name": names.get(resource_id) or resource_id,
                              "observations": observations})
    return resources


async def load_context(job) -> tuple[dict, list[dict]]:
    run = await NetworkAccessPolicyCollectionRun.filter(
        id=job.collection_run_id, tenant_id=job.tenant_id, deleted=False).first()
    if run is None or not run.raw_outputs:
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403)
    try:
        raw_outputs = orjson.loads(run.raw_outputs)
    except orjson.JSONDecodeError:
        raise CollectionError("AGENT_JOB_FORBIDDEN", 403) from None
    return raw_outputs, list(run.frozen_targets or [])
