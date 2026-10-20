"""Agent job input assembly, submission persistence and summaries."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re

import orjson

from models.network_access_control import NetworkAccessPolicyAgentJob, NetworkAccessPolicyCollectionRun
from services.action_collection.errors import CollectionError
from services.action_collection.outputs import scrub_action_output
from services.network_access_control.collection.validation import validate_document

AGENT_TIMEOUT_SECONDS = 1800
_FAILED_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


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


def check_submission(raw_outputs: dict, resource_ids: list[str], documents, failed, *, max_rules: int) -> list[dict]:
    if not isinstance(documents, list) or not isinstance(failed, list):
        return [{"resource_id": None, "message": "documents and failed must be arrays"}]
    expected = set(resource_ids)
    covered = []
    errors = []
    for item in documents:
        rid = item.get("resource_id") if isinstance(item, dict) else None
        if not isinstance(rid, str) or rid not in expected:
            errors.append({"resource_id": rid if isinstance(rid, str) else None,
                           "message": "resource is outside this job"})
            continue
        covered.append(rid)
        for message in validate_document(item.get("document"), resource_id=rid,
                                         observations=observations_for(raw_outputs, rid), max_rules=max_rules):
            errors.append({"resource_id": rid, "message": message})
    for item in failed:
        rid = item.get("resource_id") if isinstance(item, dict) else None
        if not isinstance(rid, str) or rid not in expected:
            errors.append({"resource_id": rid if isinstance(rid, str) else None,
                           "message": "failed resource is outside this job"})
            continue
        if not isinstance(item.get("code"), str) or not _FAILED_CODE.fullmatch(item["code"]):
            errors.append({"resource_id": rid, "message": "failed code is invalid"})
        covered.append(rid)
    if len(covered) != len(set(covered)) or set(covered) != expected:
        errors.append({"resource_id": None, "message": "submission must cover each job resource exactly once"})
    return errors[:50]


def clean_failed(failed: list[dict]) -> list[dict]:
    cleaned = []
    for item in failed:
        scrubbed = scrub_action_output(item.get("message") or "")
        cleaned.append({"resource_id": item["resource_id"], "code": item["code"],
                        "message": str(scrubbed)[:512]})
    return cleaned


async def store_submission(job, documents: list[dict], failed: list[dict]) -> bool:
    now = datetime.now(timezone.utc)
    changed = await NetworkAccessPolicyAgentJob.filter(
        id=job.id, tenant_id=job.tenant_id, deleted=False,
        collection_run_id=job.collection_run_id, state="RUNNING",
        job_token_hash=job.job_token_hash,
        started_at__gt=now - timedelta(seconds=AGENT_TIMEOUT_SECONDS),
    ).update(documents=orjson.dumps(documents).decode(), failed=orjson.dumps(failed).decode(),
             state="SUBMITTED", utime=now)
    return changed == 1
