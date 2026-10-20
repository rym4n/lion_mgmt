"""Frozen policy collection; later stages normalize, apply, and compare per device."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

import orjson

from models.network_access_control import NetworkAccessPolicyCollectionRun
from services.action_collection.background import start_tracked
from services.action_collection.errors import CollectionError
from services.action_collection.execute import execute_targets
from services.action_collection.outputs import scrub_action_output
from services.action_collection.targets import Target, freeze_targets
from services.network_access_control.collection import runs

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
        if await _live_in_state(run, "PENDING", repo) is not None:
            await runs.finish_run(run, "FAILED", error_code=error.code, repository=repo)
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
    repo = repository or runs.OrmPolicyRunRepository()
    try:
        succeeded = await collect_stage(plan, run, executor=executor, repository=repo)
        if not succeeded:
            return
        # Tasks 5/6 continue here: normalize -> apply -> compare -> finalize.
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
