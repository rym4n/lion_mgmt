"""Lifecycle orchestration for network topology collection runs."""
from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from core.maintenance import DEFAULT_TENANT_ID
from libs.kafka import aproduce
from libs.utils import gen_run_id
from models import NetworkTopology, Playbook, PlaybookVersion
from models.network_topology_v2 import (
    RUN_STATES,
    RUN_TERMINAL_STATES,
    TopologyAnalysisRun,
    TopologyCollectionItem,
    TopologyCollectionPlan,
    TopologyCollectionRun,
)
from services.network_topology import analysis_runs
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection.plans import BUILTIN_PLAYBOOK_NAME
from settings import TASK_TOPIC


class ActiveRunConflict(RuntimeError):
    """A database uniqueness guard rejected a second active run."""


class RunRepository(Protocol):
    async def get_plan(self, plan_id: str): ...
    async def get_plan_for_topology(self, tenant_id: str, topology_id: str): ...
    async def get_items(self, plan_id: str): ...
    async def active_run(self, tenant_id: str, topology_id: str): ...
    async def create_run(self, **values): ...
    async def get_run(self, run_id: str, *, tenant_id: str | None = None): ...
    async def save_run(self, run): ...
    async def save_plan(self, plan): ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state(value: Any) -> str:
    return getattr(value, "value", value)


def _terminal(value: Any) -> bool:
    return _state(value) in RUN_TERMINAL_STATES


def _result(run) -> dict[str, Any]:
    return {
        "collection_run_id": str(run.id),
        "state": _state(run.state),
    }


class OrmRunRepository:
    async def get_plan(self, plan_id):
        return await TopologyCollectionPlan.filter(id=plan_id, deleted=False).first()

    async def get_plan_for_topology(self, tenant_id, topology_id):
        return await TopologyCollectionPlan.filter(
            tenant_id=tenant_id, topology_id=topology_id, deleted=False
        ).first()

    async def get_items(self, plan_id):
        plan = await TopologyCollectionPlan.filter(id=plan_id, deleted=False).first()
        if plan is None:
            return []
        return await TopologyCollectionItem.filter(
            tenant_id=plan.tenant_id, plan_id=plan_id, deleted=False, enabled=True
        ).order_by("order", "id")

    async def active_run(self, tenant_id, topology_id):
        return await TopologyCollectionRun.filter(
            tenant_id=tenant_id,
            topology_id=topology_id,
            deleted=False,
            state__not_in=list(RUN_TERMINAL_STATES),
        ).order_by("-ctime").first()

    async def create_run(self, **values):
        try:
            return await TopologyCollectionRun.create(**values)
        except IntegrityError as exc:
            raise ActiveRunConflict from exc

    async def get_run(self, run_id, *, tenant_id=None):
        filters = {"id": run_id, "deleted": False}
        if tenant_id is not None:
            filters["tenant_id"] = tenant_id
        return await TopologyCollectionRun.filter(**filters).first()

    async def save_run(self, run):
        await run.save()

    async def save_plan(self, plan):
        await plan.save()

    async def finish_terminal(self, run, state, finished_at, *, error_code=None,
                              error_message=None, result=None, graph_revision=None):
        async with in_transaction() as connection:
            current = await TopologyCollectionRun.filter(
                id=run.id, tenant_id=run.tenant_id, deleted=False
            ).using_db(connection).select_for_update().first()
            if current is None or _terminal(current.state):
                return None
            plan = await TopologyCollectionPlan.filter(
                id=current.plan_id, tenant_id=current.tenant_id, deleted=False
            ).using_db(connection).select_for_update().first()
            current.state = state
            current.finished_at = finished_at
            fields = ["state", "finished_at"]
            for name, value in (("error_code", error_code), ("error_message", error_message),
                                ("result", result), ("graph_revision", graph_revision)):
                if value is not None:
                    setattr(current, name, value)
                    fields.append(name)
            if plan is not None:
                plan.last_run_id = current.id
                if state == "SUCCEEDED":
                    plan.consecutive_failures = 0
                elif state == "FAILED":
                    plan.consecutive_failures = int(plan.consecutive_failures or 0) + 1
                await plan.save(using_db=connection, update_fields=["last_run_id", "consecutive_failures"])
            await current.save(using_db=connection, update_fields=fields)
            run.state = state
            run.finished_at = finished_at
            for name in ("error_code", "error_message", "result", "graph_revision"):
                if hasattr(current, name):
                    setattr(run, name, getattr(current, name))
            return current, plan


def _repo(repository):
    return repository or OrmRunRepository()


async def _builtin_playbook():
    playbook = await Playbook.filter(
        name=BUILTIN_PLAYBOOK_NAME,
        tenant_id=DEFAULT_TENANT_ID,
        deleted=False,
        internal=True,
    ).first()
    if not playbook:
        return None, None
    version = await PlaybookVersion.filter(
        playbook_id=playbook.id,
        tenant_id=DEFAULT_TENANT_ID,
        deleted=False,
        status=1,
    ).order_by("-utime").first()
    return playbook, version


async def trigger_manual(tenant_id, topology_id, *, account_id, repository=None):
    repo = _repo(repository)
    plan = await repo.get_plan_for_topology(tenant_id, topology_id)
    if plan is None:
        items = []
    elif hasattr(repo, "get_items"):
        items = await repo.get_items(plan.id)
    else:
        items = getattr(plan, "items", [])
    if plan is None or not items:
        raise CollectionError("PLAN_EMPTY", 400)
    playbook, version = await _builtin_playbook()
    if playbook is None or version is None:
        raise CollectionError("COLLECTION_PLAYBOOK_UNAVAILABLE", 409)
    current = await repo.active_run(tenant_id, topology_id)
    if current is not None:
        data = _result(current)
        data["reused"] = True
        return data
    run_id = str(uuid.uuid4())
    try:
        run = await repo.create_run(
            id=run_id, tenant_id=tenant_id, plan_id=plan.id,
            topology_id=topology_id, trigger="manual", triggered_by=account_id,
            state="PENDING", started_at=_now(), deleted=False,
        )
    except ActiveRunConflict:
        current = await repo.active_run(tenant_id, topology_id)
        if current is not None:
            data = _result(current)
            data["reused"] = True
            return data
        raise CollectionError("COLLECTION_RUN_ACTIVE", 409)
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
    run.flow_run_id = flow_run_id
    await repo.save_run(run)
    return _result(run)


async def start_scheduled(plan_id: str, repository=None):
    repo = _repo(repository)
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise CollectionError("PLAN_NOT_FOUND", 404)
    if not plan.enabled:
        raise CollectionError("COLLECTION_PLAN_DISABLED", 409)
    current = await repo.active_run(plan.tenant_id, plan.topology_id)
    if current is not None:
        raise CollectionError("COLLECTION_RUN_ACTIVE", 409)
    try:
        run = await repo.create_run(
            id=str(uuid.uuid4()), tenant_id=plan.tenant_id, plan_id=plan.id,
            topology_id=plan.topology_id, trigger="schedule", triggered_by=None,
            state="PENDING", started_at=_now(), deleted=False,
        )
    except ActiveRunConflict:
        raise CollectionError("COLLECTION_RUN_ACTIVE", 409) from None
    return _result(run)


async def require_open_run(plan_id: str, collection_run_id: str, repository=None):
    repo = _repo(repository)
    run = await repo.get_run(collection_run_id)
    if run is None or str(run.plan_id) != str(plan_id) or _terminal(run.state):
        raise CollectionError("COLLECTION_RUN_CLOSED", 409)
    plan = await repo.get_plan(plan_id)
    if plan is None:
        raise CollectionError("COLLECTION_RUN_CLOSED", 409)
    started = getattr(run, "started_at", None)
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if started is not None and (_now() - started).total_seconds() > int(plan.run_timeout_seconds):
        await finish_run(run, "FAILED", error_code="COLLECTION_RUN_TIMEOUT", repository=repo)
        raise CollectionError("COLLECTION_RUN_CLOSED", 409)
    return plan, run


async def notify_failure(plan, run):
    """Publish configured failure notification actions using inspection conventions."""
    from services.inspection.views import _build_action_input, _extract_config_actions, _safe_int
    from services.inspection.views import _get_account_id
    actions = _extract_config_actions(getattr(plan, "notify_config", None))
    for action in actions:
        params = {
            "resource": action.get("resource", ""), "node_name": action.get("description", ""),
            "action": action.get("func", ""), "action_name": action.get("name", ""),
            "app_name": action.get("tool_name", action.get("app_name", "")),
            "name": action.get("description", ""), "app_ver": action.get("version", ""),
            "main": action.get("tool_main", ""),
            "action_input": _build_action_input(action, {"${采集结果}": str(getattr(run, "error_message", "") or "")}, {}),
            "retry_times": _safe_int(action.get("retry_times"), 3),
            "timeout": _safe_int(action.get("timeout"), 300),
            "executor": _get_account_id(getattr(run, "triggered_by", None)),
        }
        await aproduce(TASK_TOPIC, {"run_type": "action", "run_id": gen_run_id(),
                                     "run_args": params, "tenant_id": plan.tenant_id})


async def finish_run(run, state, *, error_code=None, error_message=None, result=None,
                     graph_revision=None, repository=None):
    repo = _repo(repository)
    state = _state(state)
    if state not in RUN_STATES or state not in RUN_TERMINAL_STATES:
        raise CollectionError("COLLECTION_RUN_STATE_INVALID", 400)
    finished_at = _now()
    if hasattr(repo, "finish_terminal"):
        committed = await repo.finish_terminal(
            run, state, finished_at, error_code=error_code,
            error_message=error_message, result=result, graph_revision=graph_revision,
        )
        if committed is None:
            return None
        plan = committed[1]
        if plan is not None and run.state == "FAILED" and plan.notify_enabled and plan.consecutive_failures >= 3:
            outcome = notify_failure(plan, run)
            if inspect.isawaitable(outcome):
                await outcome
        return None
    elif _terminal(run.state):
        return None
    # Set the terminal marker before the first await. This makes repeated
    # refresh/cancel/timeout calls idempotent even for repository fakes and
    # prevents duplicate failure accounting and notifications.
    run.state = state
    run.finished_at = finished_at
    if error_code is not None:
        run.error_code = error_code
    if error_message is not None:
        run.error_message = error_message
    if result is not None:
        run.result = result
    if graph_revision is not None:
        run.graph_revision = graph_revision
    plan = await repo.get_plan(run.plan_id)
    if plan is not None:
        plan.last_run_id = run.id
        if run.state == "SUCCEEDED":
            plan.consecutive_failures = 0
        elif run.state == "FAILED":
            plan.consecutive_failures = int(getattr(plan, "consecutive_failures", 0) or 0) + 1
        await repo.save_plan(plan)
    await repo.save_run(run)
    if plan is not None and run.state == "FAILED" and plan.notify_enabled and plan.consecutive_failures >= 3:
        outcome = notify_failure(plan, run)
        if inspect.isawaitable(outcome):
            await outcome


async def refresh_from_analysis(run, repository=None):
    repo = _repo(repository)
    if _state(run.state) != "SUBMITTED" or not getattr(run, "analysis_run_id", None):
        return run
    if hasattr(repo, "get_analysis"):
        analysis = await repo.get_analysis(
            run.analysis_run_id, tenant_id=run.tenant_id, topology_id=run.topology_id
        )
    else:
        analysis = await TopologyAnalysisRun.filter(
            id=run.analysis_run_id,
            tenant_id=run.tenant_id,
            topology_row_id=run.topology_id,
            deleted=False,
        ).first()
    if analysis is None:
        return run
    # Keep the in-flight analysis projection available to collection status
    # callers. Terminal transitions below still persist the collection state.
    run.analysis_state = _state(analysis.state)
    run.stage = getattr(analysis, "stage", None)
    state = _state(analysis.state)
    run._analysis_graph_revision = getattr(analysis, "graph_revision", None)
    # Expose only the public analysis error mapping; persistence summaries can
    # contain internal worker details.
    run._analysis_error_code, run._analysis_error_message = analysis_runs._public_status_error(analysis, state)
    if state == "SUCCEEDED":
        topology = None
        if hasattr(repo, "get_topology"):
            topology = await repo.get_topology(run.tenant_id, run.topology_id)
        else:
            topology = await NetworkTopology.filter(
                id=run.topology_id, tenant_id=run.tenant_id, deleted=False
            ).first()
        graph_revision = getattr(topology, "current_graph_revision", None)
        expected_revision = getattr(analysis, "expected_current_graph_revision", None)
        current_revision = getattr(topology, "current_graph_revision", None)
        result = "unchanged" if (
            expected_revision is not None
            and current_revision is not None
            and int(expected_revision) == int(current_revision)
        ) else "changed"
        await finish_run(run, "SUCCEEDED", result=result, graph_revision=graph_revision, repository=repo)
    elif state in {"FAILED", "SUPERSEDED", "CANCELLED"}:
        await finish_run(run, "FAILED", error_code=getattr(analysis, "error_code", None), repository=repo)
    return run


async def cancel_run(tenant_id, topology_id, *, account_id, repository=None):
    repo = _repo(repository)
    run = await repo.active_run(tenant_id, topology_id)
    if run is None:
        raise CollectionError("COLLECTION_RUN_NOT_FOUND", 404)
    if getattr(run, "analysis_run_id", None):
        await analysis_runs.cancel_analysis(tenant_id, run.analysis_run_id, actor=account_id, reason="用户取消采集")
    await finish_run(run, "CANCELLED", repository=repo)
    return _result(run)


async def list_runs(tenant_id, topology_id, page=1, page_size=20, repository=None):
    repo = _repo(repository)
    if hasattr(repo, "list_runs"):
        rows, total = await repo.list_runs(tenant_id, topology_id, page, page_size)
    else:
        query = TopologyCollectionRun.filter(tenant_id=tenant_id, topology_id=topology_id, deleted=False).order_by("-ctime")
        total = await query.count()
        rows = await query.offset((page - 1) * page_size).limit(page_size)
    return {"items": [_detail(row) for row in rows], "page": page, "page_size": page_size, "total": total}


def _detail(run):
    fields = ("id", "plan_id", "topology_id", "trigger", "triggered_by", "state", "flow_run_id",
              "manifest_id", "sync_id", "snapshot_id", "analysis_run_id", "device_total", "device_succeeded",
              "result", "graph_revision", "error_code", "error_message", "started_at", "finished_at")
    out = {"collection_run_id": str(run.id)}
    for field in fields[1:]:
        value = getattr(run, field, None)
        out[field] = value.isoformat() if isinstance(value, datetime) else value
    return out


async def run_detail(tenant_id, run_id, *, maintenance=False, repository=None):
    repo = _repo(repository)
    run = await repo.get_run(run_id, tenant_id=tenant_id)
    if run is None:
        raise CollectionError("COLLECTION_RUN_NOT_FOUND", 404)
    return _detail(run)


async def sweep_runs(repository=None):
    repo = _repo(repository)
    rows = await repo.open_runs() if hasattr(repo, "open_runs") else await TopologyCollectionRun.filter(deleted=False, state__not_in=list(RUN_TERMINAL_STATES))
    count = 0
    for run in rows:
        plan = await repo.get_plan(run.plan_id)
        started = getattr(run, "started_at", None)
        timeout = int(getattr(plan, "run_timeout_seconds", getattr(run, "run_timeout_seconds", 3600)) or 3600)
        if started is not None:
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if (_now() - started).total_seconds() > timeout:
                await finish_run(run, "FAILED", error_code="COLLECTION_RUN_TIMEOUT", repository=repo)
                count += 1
                continue
        if _state(run.state) == "SUBMITTED":
            await refresh_from_analysis(run, repository=repo)
            count += 1
    return count
