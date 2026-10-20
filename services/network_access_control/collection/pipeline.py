"""Frozen policy collection; later stages normalize, apply, and compare per device."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

import orjson

from libs.error_code.exception import CustomError
from models.network_access_control import NetworkAccessPolicyCollectionRun
from services.action_collection.background import start_tracked
from services.action_collection.errors import CollectionError
from services.action_collection.execute import execute_targets
from services.action_collection.outputs import scrub_action_output
from services.action_collection.targets import Target, freeze_targets
from services.network_access_control import manager
from services.network_access_control.collection import runs
from services.network_access_control.collection.constants import SYNC_SOURCE
from services.network_access_control.collection.limits import max_rules_per_device

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NormalizeResult:
    resource_id: str
    status: str
    document: dict | None
    error_code: str | None


class Normalizer(Protocol):
    async def __call__(self, plan, run, resource_ids: list[str]) -> dict[str, NormalizeResult]: ...


def default_normalizer() -> Normalizer:
    # Agent-stage integration is deliberately loaded only when normalization starts.
    from services.network_access_control.collection.agent_stage import build_agent_normalizer
    return build_agent_normalizer()


async def _live_in_state(run, state, repository):
    """Check persisted state at a side-effect boundary, refreshing stale callers."""
    current = await repository.get_run(run.id, tenant_id=run.tenant_id)
    if current is None:
        return None
    run.state = current.state
    return current if current.state == state else None


def _device(target: Target) -> dict:
    return {"resource_id": target.resource_id, "resource_name": target.resource_name,
            "device_asset_id": target.asset_instance_id, "app_name": target.app_name,
            "action_func": target.action_func, "item_id": target.item_id, "stage": "collect",
            "status": "skipped", "error_code": None, "error_message": None, "policy_count": None,
            "sync": None, "duration_ms": None, "lion_action_run_id": None}


async def freeze_and_claim(plan, run, *, target_repository=None, repository=None) -> bool:
    repo = repository or runs.OrmPolicyRunRepository()
    if await _live_in_state(run, "PENDING", repo) is None:
        return False
    try:
        targets = await freeze_targets(plan, await repo.get_items(plan), repository=target_repository)
        if not targets:
            raise CollectionError("PLAN_EMPTY", 400)
    except CollectionError as error:
        if not await runs.finish_run(run, "FAILED", error_code=error.code,
                                     expected_state="PENDING", repository=repo):
            return False
        # Target errors may name resources; only the stable code crosses the API.
        raise CollectionError(error.code, error.status) from None
    return await repo.transition(run, "PENDING", "COLLECTING", {
        "frozen_targets": [asdict(target) for target in targets], "device_total": len(targets),
        "heartbeat_at": datetime.now(timezone.utc)})


async def collect_stage(plan, run, *, executor=None, repository=None) -> list[dict]:
    repo = repository or runs.OrmPolicyRunRepository()
    current = await _live_in_state(run, "COLLECTING", repo)
    if current is None:
        return []
    targets = [Target(**target) for target in current.frozen_targets or []]
    outcomes = await execute_targets(
        run.tenant_id, targets, concurrency=int(plan.concurrency),
        per_item_timeout=int(plan.per_item_timeout_seconds), executor_id=runs.operator_for(current),
        executor=executor)
    # Cancellation/sweep may have won while device actions were in flight. Their
    # outputs must not be persisted or passed to the later normalization stage.
    if await _live_in_state(run, "COLLECTING", repo) is None:
        return []
    devices = {target.resource_id: _device(target) for target in targets}
    raw_outputs: dict[str, list[dict]] = {}
    for outcome in outcomes:
        device = devices[outcome.resource_id]
        device.update(status=outcome.status, error_code=outcome.error_code,
                      error_message=outcome.error_message, duration_ms=outcome.duration_ms,
                      lion_action_run_id=outcome.action_run_id)
        if outcome.status == "success":
            raw_outputs.setdefault(outcome.resource_id, []).append({
                "item_id": outcome.item_id, "app_name": outcome.app_name, "action_func": outcome.action_func,
                "app_version": outcome.app_version, "output": scrub_action_output(outcome.output)})
    results = list(devices.values())
    succeeded = [device for device in results if device["status"] == "success"]
    if not succeeded:
        await runs.finish_run(run, "FAILED", error_code="COLLECTION_INCOMPLETE",
                              values={"item_results": results, "device_succeeded": 0}, repository=repo)
        return []
    moved = await repo.transition(run, "COLLECTING", "NORMALIZING", {
        "item_results": results, "device_succeeded": len(succeeded),
        "raw_outputs": orjson.dumps(raw_outputs).decode()})
    return succeeded if moved else []


async def run_pipeline(plan, run, *, executor=None, normalizer=None, repository=None) -> None:
    run._normalization_claimed = False
    repo = repository or runs.OrmPolicyRunRepository()
    try:
        succeeded = await collect_stage(plan, run, executor=executor, repository=repo)
        if not succeeded:
            return
        normalized = await normalize_stage(plan, run, succeeded,
                                           normalizer=normalizer or default_normalizer(), repository=repo)
        if not run._normalization_claimed:
            return
        applied = await apply_stage(plan, run, normalized, repository=repo) if normalized else []
        if await _live_in_state(run, "APPLYING", repo) is None:
            return
        await compare_stage(plan, run, applied, repository=repo)
        await finalize(run, repository=repo)
    except CollectionError as error:
        await runs.finish_run(run, "FAILED", error_code=error.code, repository=repo)
    except Exception:
        # Never persist/log exception text: executors may echo raw credentials.
        logger.error("Policy collection pipeline failed for run %s", run.id)
        await runs.finish_run(run, "FAILED", error_code="COLLECTION_DISPATCH_FAILED", repository=repo)


async def start_pipeline(plan, run, *, repository=None) -> dict:
    repo = repository or runs.OrmPolicyRunRepository()
    if await freeze_and_claim(plan, run, repository=repo):
        if await _live_in_state(run, "COLLECTING", repo) is not None:
            start_tracked(run_pipeline(plan, run, repository=repo), run=run,
                          run_model=NetworkAccessPolicyCollectionRun)
    else:
        current = await repo.get_run(run.id, tenant_id=run.tenant_id)
        if current is not None:
            run.state = current.state
    return {"collection_run_id": str(run.id), "run_state": run.state}


FIELD_LIMITS = {"rule_name": 255, "source_zone": 100, "source_address": 255, "target_zone": 100,
                "target_address": 255, "protocol": 30, "port": 100}
_TEXT_FIELDS = ("policy_key", "raw_rule_id", "rule_name", "source_zone", "source_address", "target_zone",
                "target_address", "protocol")
_DETAIL_FIELDS = ("source_port", "destination_port", "service", "policy_order")
_PUBLIC_RESULT_KEYS = ("_document",)


def _fit(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def policy_item(policy: dict) -> dict:
    """Map one normalized policy onto the ledger payload without losing long values."""
    detail = {key: policy[key] for key in _DETAIL_FIELDS if policy.get(key) is not None}
    if "action_inferred" in policy:
        detail["action_inferred"] = policy["action_inferred"]
    item = {key: policy[key] for key in _TEXT_FIELDS if policy.get(key) is not None}
    if policy.get("destination_port") is not None:
        item["port"] = policy["destination_port"]
    for key, limit in FIELD_LIMITS.items():
        value = item.get(key)
        if isinstance(value, str) and len(value) > limit:
            detail[f"full_{key}"] = value
            item[key] = _fit(value, limit)
    item.update(action=policy["action"], enabled=policy.get("enabled", True),
                parse_status=policy["parse_status"], raw_text=policy["raw_text"], policy_data=detail)
    return item


def _stored(devices: list[dict]) -> list[dict]:
    return [{k: v for k, v in d.items() if k not in _PUBLIC_RESULT_KEYS} for d in devices]


def _merge(run, updates: list[dict]) -> list[dict]:
    by_id = {d["resource_id"]: d for d in updates}
    return [by_id.get(d["resource_id"], d) for d in run.item_results or []]


async def normalize_stage(plan, run, devices, *, normalizer, repository) -> list[dict]:
    # An empty result must distinguish all-device failures from a lost CAS.
    # This attempt-local marker is never part of a repository/public payload.
    run._normalization_claimed = False
    if await _live_in_state(run, "NORMALIZING", repository) is None:
        return []
    ids = [d["resource_id"] for d in devices]
    try:
        results = await normalizer(plan, run, ids)
    except CollectionError as error:
        results = {rid: NormalizeResult(rid, "failed", None, error.code) for rid in ids}
    except Exception:
        logger.error("Policy normalizer failed for run %s", run.id)
        results = {rid: NormalizeResult(rid, "failed", None, "AGENT_UNAVAILABLE") for rid in ids}
    if await _live_in_state(run, "NORMALIZING", repository) is None:
        return []
    updated, ok = [], []
    for d in devices:
        result = results.get(d["resource_id"])
        entry = {**d, "stage": "normalize"}
        if result is None or result.status != "success" or not isinstance(result.document, dict):
            entry.update(status="failed",
                         error_code=(result.error_code if result and result.error_code else "POLICY_NORMALIZE_FAILED"))
        else:
            entry.update(status="success", error_code=None,
                         policy_count=len(result.document.get("policies") or []))
            ok.append({**entry, "_document": result.document})
        updated.append(entry)
    moved = await repository.transition(run, "NORMALIZING", "APPLYING", {"item_results": _stored(_merge(run, updated))})
    run._normalization_claimed = bool(moved)
    return ok if moved else []


async def apply_stage(plan, run, devices, *, repository) -> list[dict]:
    if await _live_in_state(run, "APPLYING", repository) is None:
        return []
    max_items = await max_rules_per_device()
    if await _live_in_state(run, "APPLYING", repository) is None:
        return []
    operator = runs.operator_for(run)
    updated, applied = [], []
    for d in devices:
        if await _live_in_state(run, "APPLYING", repository) is None:
            return []
        document = d["_document"]
        policies = document.get("policies") or []
        device_key = f"resource:{d['resource_id']}"
        entry = {k: v for k, v in d.items() if k != "_document"} | {"stage": "apply"}
        existing_count = 0
        if not policies:
            existing_count = await repository.count_policies(run.tenant_id, device_key)
            if await _live_in_state(run, "APPLYING", repository) is None:
                return []
        if not policies and existing_count > 0:
            entry.update(status="failed", error_code="POLICY_EMPTY_RESULT")
        elif len(policies) > max_items:
            entry.update(status="failed", error_code="POLICY_SYNC_LIMIT")
        else:
            try:
                counts = await manager.apply_actual_policies(run.tenant_id, {
                    "device_key": device_key, "device_asset_id": d.get("device_asset_id"),
                    "device_name": d.get("resource_name"), "device_type": document.get("device_type"),
                    "policy_type": document.get("policy_type"), "collection_run_id": str(run.id),
                    "sync_source": SYNC_SOURCE, "batch_id": f"{run.id}:{d['resource_id']}",
                    "replace": True, "policies": [policy_item(p) for p in policies],
                }, operator, max_items=max_items)
                if await _live_in_state(run, "APPLYING", repository) is None:
                    return []
                entry.update(status="success", error_code=None,
                             sync={k: counts.get(k, 0) for k in ("added", "modified", "moved", "deleted", "unchanged")})
                applied.append(entry)
            except (CustomError, ValueError, TypeError):
                entry.update(status="failed", error_code="POLICY_SYNC_FAILED")
            except Exception:
                logger.error("Policy apply failed for run %s resource %s", run.id, d["resource_id"])
                entry.update(status="failed", error_code="POLICY_SYNC_FAILED")
        if await _live_in_state(run, "APPLYING", repository) is None:
            return []
        updated.append(entry)
    if await _live_in_state(run, "APPLYING", repository) is None:
        return []
    await repository.save_fields(run, {"item_results": _stored(_merge(run, updated))})
    return applied if await _live_in_state(run, "APPLYING", repository) is not None else []


def _final_state(devices: list[dict]) -> tuple[str, str | None]:
    applied = [d for d in devices if d.get("stage") == "apply" and d.get("status") == "success"]
    if applied and len(applied) == len(devices):
        return "SUCCEEDED", None
    if applied:
        return "PARTIAL", None
    codes = Counter(d.get("error_code") for d in devices if d.get("error_code"))
    # most_common keeps first-seen order on ties, i.e. frozen device order.
    return "FAILED", (codes.most_common(1)[0][0] if codes else "COLLECTION_INCOMPLETE")


async def finalize(run, *, repository) -> None:
    current = await _live_in_state(run, "APPLYING", repository)
    if current is None:
        return
    devices = list(current.item_results or [])
    state, code = _final_state(devices)
    applied = sum(1 for d in devices if d.get("stage") == "apply" and d.get("status") == "success")
    await runs.finish_run(run, state, error_code=code, values={"device_succeeded": applied},
                          expected_state="APPLYING", repository=repository)


async def compare_stage(plan, run, applied, *, repository) -> None:
    # Task 6 supplies comparison; apply success determines collection success.
    return None
