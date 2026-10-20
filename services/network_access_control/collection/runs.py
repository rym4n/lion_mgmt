# services/network_access_control/collection/runs.py
"""Lifecycle of network policy collection runs (one active run per tenant)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from core.maintenance import DEFAULT_TENANT_ID
from libs.kafka import aproduce
from libs.utils import gen_run_id
from models.network_access_control import (
    POLICY_RUN_STATES,
    POLICY_RUN_TERMINAL_STATES,
    NetworkAccessActualPolicy,
    NetworkAccessPolicyCollectionItem,
    NetworkAccessPolicyCollectionPlan,
    NetworkAccessPolicyCollectionRun,
)
from services.action_collection import timer as collection_timer
from services.action_collection.errors import CollectionError
from services.action_collection.notify import notify_after_commit
from services.network_access_control.collection.constants import AUTO_OPERATOR, BUILTIN_PLAYBOOK_NAME
from settings import TASK_TOPIC

logger = logging.getLogger(__name__)

SUMMARY_FIELDS = ("trigger", "triggered_by", "state", "device_total", "device_succeeded", "compare_run_id",
                  "error_code", "error_message", "started_at", "finished_at")
DEVICE_FIELDS = ("resource_id", "resource_name", "device_asset_id", "app_name", "action_func", "stage",
                 "status", "error_code", "policy_count", "sync", "duration_ms")
SYNC_FIELDS = ("added", "modified", "moved", "deleted", "unchanged")
RUN_VALUE_FIELDS = frozenset({
    "flow_run_id", "frozen_targets", "raw_outputs", "item_results", "device_total", "device_succeeded",
    "agent_job_id", "compare_run_id", "compare_error", "error_code", "error_message", "heartbeat_at",
})


def _validate_values(values):
    # Identity and lifecycle columns belong to the repository guards, never a
    # worker payload (which could otherwise bypass CAS or terminal accounting).
    if values.keys() - RUN_VALUE_FIELDS:
        raise ValueError("Unsupported collection run update fields")


class ActiveRunConflict(RuntimeError):
    """The partial unique index rejected a second active run for the tenant."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _terminal(state) -> bool:
    return state in POLICY_RUN_TERMINAL_STATES


def operator_for(run) -> str:
    return str(getattr(run, "triggered_by", None) or AUTO_OPERATOR)


def account_plan(plan, run) -> None:
    """Apply terminal accounting to an in-memory plan row."""
    plan.last_run_id = run.id
    if run.state in ("SUCCEEDED", "PARTIAL"):
        plan.consecutive_failures = 0
    elif run.state == "FAILED":
        plan.consecutive_failures = int(plan.consecutive_failures or 0) + 1


class OrmPolicyRunRepository:
    async def get_plan(self, plan_id, *, tenant_id=None):
        filters = {"id": plan_id, "deleted": False}
        if tenant_id is not None:
            filters["tenant_id"] = tenant_id
        return await NetworkAccessPolicyCollectionPlan.filter(**filters).first()

    async def get_plan_for_tenant(self, tenant_id):
        return await NetworkAccessPolicyCollectionPlan.filter(tenant_id=tenant_id, deleted=False).first()

    async def get_items(self, plan):
        return await NetworkAccessPolicyCollectionItem.filter(
            tenant_id=plan.tenant_id, plan_id=plan.id, deleted=False, enabled=True).order_by("order", "id")

    async def active_run(self, tenant_id):
        return await NetworkAccessPolicyCollectionRun.filter(
            tenant_id=tenant_id, deleted=False, state__not_in=list(POLICY_RUN_TERMINAL_STATES),
        ).order_by("-ctime").first()

    async def create_run(self, **values):
        if not values.get("tenant_id"):
            raise ValueError("Collection runs require an explicit tenant")
        try:
            return await NetworkAccessPolicyCollectionRun.create(**values)
        except IntegrityError as exc:
            raise ActiveRunConflict from exc

    async def get_run(self, run_id, *, tenant_id=None):
        filters = {"id": run_id, "deleted": False}
        if tenant_id is not None:
            filters["tenant_id"] = tenant_id
        return await NetworkAccessPolicyCollectionRun.filter(**filters).first()

    async def transition(self, run, from_state, to_state, values):
        _validate_values(values)
        if _terminal(from_state) or _terminal(to_state):
            return False
        if from_state not in POLICY_RUN_STATES or to_state not in POLICY_RUN_STATES:
            raise ValueError("Invalid collection run state")
        changed = await NetworkAccessPolicyCollectionRun.filter(
            id=run.id, tenant_id=run.tenant_id, deleted=False, state=from_state,
        ).update(state=to_state, **values)
        if changed:
            run.state = to_state
            for key, value in values.items():
                setattr(run, key, value)
        return bool(changed)

    async def save_fields(self, run, values):
        _validate_values(values)
        changed = await NetworkAccessPolicyCollectionRun.filter(
            id=run.id, tenant_id=run.tenant_id, deleted=False,
            state__not_in=list(POLICY_RUN_TERMINAL_STATES),
        ).update(**values)
        if changed:
            for key, value in values.items():
                setattr(run, key, value)
        return bool(changed)

    async def finish_terminal(self, run, state, finished_at, values):
        _validate_values(values)
        if not _terminal(state):
            raise ValueError("Expected a terminal collection run state")
        async with in_transaction() as connection:
            current = await NetworkAccessPolicyCollectionRun.filter(
                id=run.id, tenant_id=run.tenant_id, deleted=False,
            ).using_db(connection).select_for_update().first()
            if current is None or _terminal(current.state):
                if current is not None:
                    # Refresh a stale caller after cancellation/sweep won the
                    # lock, so its response does not claim its requested state.
                    run.state, run.finished_at = current.state, current.finished_at
                return None
            plan = await NetworkAccessPolicyCollectionPlan.filter(
                id=current.plan_id, tenant_id=current.tenant_id, deleted=False,
            ).using_db(connection).select_for_update().first()
            current.state = state
            current.finished_at = finished_at
            for key, value in values.items():
                setattr(current, key, value)
            await current.save(using_db=connection, update_fields=["state", "finished_at", *values])
            if plan is not None:
                account_plan(plan, current)
                await plan.save(using_db=connection, update_fields=["last_run_id", "consecutive_failures"])
        run.state, run.finished_at = state, finished_at
        for key, value in values.items():
            setattr(run, key, value)
        return plan

    async def list_runs(self, tenant_id, page, page_size):
        query = NetworkAccessPolicyCollectionRun.filter(tenant_id=tenant_id, deleted=False).order_by("-ctime")
        return await query.offset((page - 1) * page_size).limit(page_size), await query.count()

    async def open_runs(self):
        return await NetworkAccessPolicyCollectionRun.filter(
            deleted=False, state__not_in=list(POLICY_RUN_TERMINAL_STATES))

    async def count_policies(self, tenant_id, device_key):
        return await NetworkAccessActualPolicy.filter(
            tenant_id=tenant_id, device_key=device_key, deleted=False).count()

    async def clear_expired_raw_outputs(self, before):
        return await NetworkAccessPolicyCollectionRun.filter(
            deleted=False, state__in=list(POLICY_RUN_TERMINAL_STATES), finished_at__lt=before,
            raw_outputs__isnull=False,
        ).update(raw_outputs=None)


def _repo(repository):
    return repository or OrmPolicyRunRepository()


def _result(run) -> dict:
    return {"collection_run_id": str(run.id), "state": run.state}


async def finish_run(run, state, *, error_code=None, error_message=None, values=None, repository=None):
    if state not in POLICY_RUN_STATES or state not in POLICY_RUN_TERMINAL_STATES:
        raise CollectionError("COLLECTION_RUN_STATE_INVALID", 400)
    payload = dict(values or {})
    if error_code is not None:
        payload["error_code"] = error_code
    if error_message is not None:
        payload["error_message"] = error_message
    plan = await _repo(repository).finish_terminal(run, state, _now(), payload)
    if plan is not None:
        await notify_after_commit(plan, run)


async def _playbook_version():
    playbook, version = await collection_timer.builtin_playbook(BUILTIN_PLAYBOOK_NAME)
    if playbook is None or version is None:
        raise CollectionError("COLLECTION_PLAYBOOK_UNAVAILABLE", 409)
    return version


async def trigger_manual(tenant_id, *, account_id, repository=None):
    repo = _repo(repository)
    plan = await repo.get_plan_for_tenant(tenant_id)
    if plan is None or not await repo.get_items(plan):
        raise CollectionError("PLAN_EMPTY", 400)
    version = await _playbook_version()
    current = await repo.active_run(tenant_id)
    if current is not None:
        return {**_result(current), "reused": True}
    try:
        run = await repo.create_run(id=str(uuid.uuid4()), tenant_id=tenant_id, plan_id=plan.id, trigger="manual",
                                    triggered_by=account_id, state="PENDING", started_at=_now(), deleted=False)
    except ActiveRunConflict:
        current = await repo.active_run(tenant_id)
        if current is not None:
            return {**_result(current), "reused": True}
        raise CollectionError("COLLECTION_RUN_ACTIVE", 409) from None
    flow_run_id = gen_run_id()
    try:
        await aproduce(TASK_TOPIC, {
            "run_type": "flow", "run_id": flow_run_id,
            "run_args": {"flow_id": version.id,
                         "flow_input": {"plan_id": plan.id, "collection_run_id": run.id},
                         "executor": account_id},
            "tenant_id": DEFAULT_TENANT_ID,
        }, raise_on_error=True)
    except Exception:
        await finish_run(run, "FAILED", error_code="COLLECTION_FLOW_SUBMIT_FAILED", repository=repo)
        raise
    await repo.save_fields(run, {"flow_run_id": flow_run_id})
    return _result(run)


async def start_scheduled(plan_id, *, repository=None):
    repo = _repo(repository)
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise CollectionError("PLAN_NOT_FOUND", 404)
    if not plan.enabled:
        raise CollectionError("COLLECTION_PLAN_DISABLED", 409)
    if await repo.active_run(plan.tenant_id) is not None:
        raise CollectionError("COLLECTION_RUN_ACTIVE", 409)
    try:
        run = await repo.create_run(id=str(uuid.uuid4()), tenant_id=plan.tenant_id, plan_id=plan.id,
                                    trigger="schedule", triggered_by=None, state="PENDING",
                                    started_at=_now(), deleted=False)
    except ActiveRunConflict:
        raise CollectionError("COLLECTION_RUN_ACTIVE", 409) from None
    return _result(run)


async def get_run_for_plan(plan_id, collection_run_id, *, repository=None):
    repo = _repo(repository)
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise CollectionError("PLAN_NOT_FOUND", 404)
    run = await repo.get_run(collection_run_id, tenant_id=plan.tenant_id)
    if run is None or str(run.plan_id) != str(plan.id):
        raise CollectionError("COLLECTION_RUN_NOT_FOUND", 404)
    return plan, run


async def require_open_run(plan_id, collection_run_id, *, repository=None):
    repo = _repo(repository)
    try:
        plan, run = await get_run_for_plan(plan_id, collection_run_id, repository=repo)
    except CollectionError:
        raise CollectionError("COLLECTION_RUN_CLOSED", 409) from None
    if _terminal(run.state):
        raise CollectionError("COLLECTION_RUN_CLOSED", 409)
    started = _aware(run.started_at)
    if started is not None and (_now() - started).total_seconds() > int(plan.run_timeout_seconds):
        await finish_run(run, "FAILED", error_code="COLLECTION_RUN_TIMEOUT", repository=repo)
        raise CollectionError("COLLECTION_RUN_CLOSED", 409)
    return plan, run


async def cancel_run(tenant_id, *, account_id, repository=None):
    repo = _repo(repository)
    run = await repo.active_run(tenant_id)
    if run is None:
        raise CollectionError("COLLECTION_RUN_NOT_FOUND", 404)
    await finish_run(run, "CANCELLED", repository=repo)
    return _result(run)


def _text(value):
    return _aware(value).isoformat() if isinstance(value, datetime) else value


def run_summary(run) -> dict:
    return {"collection_run_id": str(run.id), **{f: _text(getattr(run, f, None)) for f in SUMMARY_FIELDS}}


def public_devices(item_results) -> list[dict]:
    # Executor messages may echo credentials; only stable codes cross the API.
    if not isinstance(item_results, list):
        return []
    devices = []
    for entry in item_results:
        if not isinstance(entry, dict):
            continue
        device = {name: entry.get(name) for name in DEVICE_FIELDS}
        sync = device["sync"]
        device["sync"] = {name: sync.get(name) for name in SYNC_FIELDS} if isinstance(sync, dict) else None
        devices.append(device)
    return devices


def status_projection(run) -> dict:
    return {"run_state": run.state, "terminal": _terminal(run.state), "device_total": run.device_total,
            "device_succeeded": run.device_succeeded, "compare_run_id": run.compare_run_id,
            "error_code": run.error_code, "error_message": run.error_message,
            "devices": public_devices(run.item_results)}


async def list_runs(tenant_id, page, page_size, *, repository=None):
    rows, total = await _repo(repository).list_runs(tenant_id, page, page_size)
    return {"items": [run_summary(row) for row in rows], "page": page, "page_size": page_size, "total": total}


async def run_detail(tenant_id, run_id, *, maintenance=False, repository=None):
    run = await _repo(repository).get_run(run_id, tenant_id=tenant_id)
    if run is None:
        raise CollectionError("COLLECTION_RUN_NOT_FOUND", 404)
    # 03 replaces "agent" with agent_jobs.policy_agent_summary(...).
    return {**run_summary(run), "devices": public_devices(run.item_results), "agent": None}
