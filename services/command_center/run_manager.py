# coding=utf-8
import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
from typing import Any

from models.command_center import (
    COMMAND_RUN_STATUS_CANCELLED,
    COMMAND_RUN_STATUS_CHECKED_OUT,
    COMMAND_RUN_STATUS_COMPLETED,
    COMMAND_RUN_STATUS_FAILED,
    COMMAND_RUN_STATUS_HANDED_OFF,
    COMMAND_RUN_STATUS_RUNNING,
    COMMAND_RUN_STATUS_STALE,
    COMMAND_RUN_STATUS_WAITING_APPROVAL,
    COMMAND_RUNTIME_LION_AGENT,
    COMMAND_TASK_STATUS_CANCELLED,
    COMMAND_TASK_STATUS_COMPLETED,
    COMMAND_TASK_STATUS_DISPATCHED,
    COMMAND_TASK_STATUS_FAILED,
    COMMAND_TASK_STATUS_HANDED_OFF,
    COMMAND_TASK_STATUS_PENDING_APPROVAL,
    CommandAgent,
    CommandTask,
    CommandTaskRun,
)
from settings import TZ

from services.command_center.approval_contract import CONTINUATION_MODE
from services.digital_employee.prompt_builder import build_workspace_snapshots


DEFAULT_LEASE_SECONDS = 300
ACTIVE_RUN_STATUSES = {
    COMMAND_RUN_STATUS_CHECKED_OUT,
    COMMAND_RUN_STATUS_RUNNING,
    COMMAND_RUN_STATUS_WAITING_APPROVAL,
}
TERMINAL_RUN_STATUSES = {
    COMMAND_RUN_STATUS_COMPLETED,
    COMMAND_RUN_STATUS_FAILED,
    COMMAND_RUN_STATUS_CANCELLED,
    COMMAND_RUN_STATUS_STALE,
    COMMAND_RUN_STATUS_HANDED_OFF,
}
RuntimeEventHandler = Callable[[str, dict], Awaitable[dict | None]]
RunCheckedOutHandler = Callable[[CommandTaskRun], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(TZ)


def _event_payload(payload: Any) -> dict:
    return payload if isinstance(payload, dict) else {"value": payload}


def _runtime_control_action(payload: dict) -> dict:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    action = data.get("requested_action") if isinstance(data.get("requested_action"), dict) else data
    return action if isinstance(action, dict) else {}


def _runtime_tool_name(data: dict) -> str | None:
    return data.get("name") or data.get("tool_name") or data.get("tool") or data.get("tool_call_name")


def normalize_runtime_event(event_type: str, payload: Any) -> dict:
    data = _event_payload(payload)
    if event_type == "delta":
        return {
            "event_type": "runtime_delta",
            "message": data.get("content") or data.get("delta"),
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    if event_type == "thinking":
        return {
            "event_type": "runtime_thinking",
            "message": data.get("content"),
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    if event_type == "tool_call":
        tool_data = data.get("data") if isinstance(data.get("data"), dict) else data
        tool_name = _runtime_tool_name(tool_data)
        status = data.get("status") or tool_data.get("status") or "started"
        return {
            "event_type": "runtime_tool_call",
            "message": f"{tool_name or 'tool'} {status}",
            "tool_call": {"tool_name": tool_name, "status": status, "data": tool_data},
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    if event_type == "approval_required":
        requested_action = _runtime_control_action(data)
        return {
            "event_type": "approval_required",
            "message": "数字员工请求人工审批",
            "run_status": COMMAND_RUN_STATUS_WAITING_APPROVAL,
            "task_status": COMMAND_TASK_STATUS_PENDING_APPROVAL,
            "requested_action": requested_action,
            "metadata": {"runtime_event": event_type, "payload": data, "requested_action": requested_action},
        }
    if event_type == "policy_denied":
        denied_action = _runtime_control_action(data)
        return {
            "event_type": "policy_denied",
            "message": data.get("message") or "策略拒绝本次运行",
            "run_status": COMMAND_RUN_STATUS_FAILED,
            "task_status": COMMAND_TASK_STATUS_FAILED,
            "error": {"message": data.get("message") or "policy denied", "payload": data},
            "requested_action": denied_action,
            "metadata": {"runtime_event": event_type, "payload": data, "requested_action": denied_action},
        }
    if event_type == "error":
        return {
            "event_type": "runtime_error",
            "message": data.get("message") or str(data),
            "run_status": COMMAND_RUN_STATUS_FAILED,
            "task_status": COMMAND_TASK_STATUS_FAILED,
            "error": {"message": data.get("message") or str(data), "payload": data},
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    if event_type == "end":
        return {
            "event_type": "runtime_completed",
            "message": "数字员工运行完成",
            "metadata": {"runtime_event": event_type, "payload": data},
        }
    return {
        "event_type": "runtime_progress",
        "message": data.get("message"),
        "metadata": {"runtime_event": event_type, "payload": data},
    }


async def _workspace_snapshots(employee: CommandAgent) -> dict:
    role = await employee.role
    if role is None:
        return build_workspace_snapshots(employee, None, [])
    from services.digital_employee.manager import get_visible_skills

    skills = await get_visible_skills(employee.tenant_id, employee.skill_keys or [])
    return build_workspace_snapshots(employee, role, skills)


async def checkout_run(
    task: CommandTask,
    employee: CommandAgent,
    operator_id: str | None = None,
    lease_owner: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    metadata: dict | None = None,
    policy_snapshot_override: dict | None = None,
    workspace_etag_override: str | None = None,
) -> CommandTaskRun:
    from services.command_center.manager import record_task_event

    now = _now()
    snapshots = await _workspace_snapshots(employee)
    run_metadata = {"operator_id": operator_id, "continuation_mode": CONTINUATION_MODE}
    if metadata:
        run_metadata.update(metadata)
    run = await CommandTaskRun.create(
        tenant_id=task.tenant_id,
        task=task,
        employee=employee,
        run_id=uuid.uuid4().hex,
        runtime=COMMAND_RUNTIME_LION_AGENT,
        status=COMMAND_RUN_STATUS_CHECKED_OUT,
        lease_owner=lease_owner or f"lion_mgmt:{task.id}",
        lease_expires_at=now + timedelta(seconds=lease_seconds),
        heartbeat_at=now,
        workspace_etag=workspace_etag_override or snapshots["workspace_etag"],
        manifest_snapshot=snapshots["manifest_snapshot"],
        policy_snapshot=policy_snapshot_override or snapshots["policy_snapshot"],
        started_at=now,
        metadata=run_metadata,
    )
    task.run_id = run.run_id
    await task.save(update_fields=["run_id", "utime"])
    await record_task_event(
        task,
        "run_checked_out",
        "数字员工运行已 checkout",
        operator_id,
        {"run_id": run.run_id, "lease_owner": run.lease_owner, "workspace_etag": run.workspace_etag},
    )
    return run


def _read_only_policy_snapshot(policy_snapshot: dict) -> dict:
    policy = deepcopy(policy_snapshot or {})
    policy["continuation_mutation"] = {
        "mode": "read_only",
        "filesystem": {"write": "deny", "delete": "deny"},
        "tool_permissions": {"writes": "deny", "mutations": "deny"},
    }
    filesystem = policy.get("filesystem") if isinstance(policy.get("filesystem"), dict) else {}
    policy["filesystem"] = {**filesystem, "write": "deny", "delete": "deny"}
    tool_permissions = policy.get("tool_permissions") if isinstance(policy.get("tool_permissions"), dict) else {}
    rules = tool_permissions.get("rules") if isinstance(tool_permissions.get("rules"), dict) else {}
    policy["tool_permissions"] = {
        **tool_permissions,
        "mode": "read_only",
        "rules": {**rules, "write": "deny", "delete": "deny", "mutate": "deny"},
    }
    approval = policy.get("approval") if isinstance(policy.get("approval"), dict) else {}
    skill_policy = dict(approval.get("skills") or {})
    for skill_key, decision in list(skill_policy.items()):
        if decision == "allow":
            skill_policy[skill_key] = "approval_required"
    policy["approval"] = {**approval, "default": "approval_required", "skills": skill_policy}
    return policy


async def _continuation_workspace(
    employee: CommandAgent,
    approved_policy_mutation: str | None,
    continuation_approval_id: str | None,
) -> dict | None:
    if approved_policy_mutation != "read_only" or not continuation_approval_id:
        return None
    suffix = hashlib.sha256(f"{employee.id}:{continuation_approval_id}:read_only".encode("utf-8")).hexdigest()[:12]
    workspace_id = f"{employee.id}:approval-{suffix}:read-only"
    role = await employee.role
    if role is None:
        from services.digital_employee.prompt_builder import build_workspace_payload, render_workspace_files

        payload = build_workspace_payload(employee, None, [])
        files = render_workspace_files(employee, None, [])
        policy_snapshot = _read_only_policy_snapshot(payload["policy"])
        files["policy.json"] = policy_snapshot
        workspace_etag = f"read-only-{suffix}"
        manifest = dict(payload["manifest"])
        manifest["workspace_etag"] = workspace_etag
        manifest["continuation_mutation"] = {"mode": "read_only", "approval_id": continuation_approval_id}
        files["manifest.json"] = manifest
    else:
        from services.digital_employee.manager import get_visible_skills
        from services.digital_employee.prompt_builder import build_workspace_payload, render_workspace_files, workspace_etag

        skills = await get_visible_skills(employee.tenant_id, employee.skill_keys or [])
        payload = build_workspace_payload(employee, role, skills)
        policy_snapshot = _read_only_policy_snapshot(payload["policy"])
        files = render_workspace_files(employee, role, skills)
        files["policy.json"] = policy_snapshot
        role_version = getattr(role, "version", None)
        skill_versions = {skill.skill_key: skill.version for skill in skills}
        etag = workspace_etag(role, skills, employee)
        workspace_etag = f"{etag[:32]}-ro-{suffix}"
        manifest = dict(payload["manifest"])
        manifest["workspace_etag"] = workspace_etag
        manifest["continuation_mutation"] = {"mode": "read_only", "approval_id": continuation_approval_id}
        files["manifest.json"] = manifest
        return {
            "workspace_id": workspace_id,
            "files": files,
            "role_version": role_version,
            "skill_versions": skill_versions,
            "workspace_etag": workspace_etag,
            "policy_snapshot": policy_snapshot,
        }
    return {
        "workspace_id": workspace_id,
        "files": files,
        "role_version": None,
        "skill_versions": {},
        "workspace_etag": workspace_etag,
        "policy_snapshot": policy_snapshot,
    }


async def heartbeat_run(run: CommandTaskRun, normalized_event: dict | None = None) -> CommandTaskRun:
    run.heartbeat_at = _now()
    update_fields = ["heartbeat_at", "utime"]
    if run.status == COMMAND_RUN_STATUS_CHECKED_OUT:
        run.status = COMMAND_RUN_STATUS_RUNNING
        update_fields.append("status")
    if normalized_event:
        if normalized_event.get("run_status"):
            run.status = normalized_event["run_status"]
            update_fields.append("status")
        if normalized_event.get("tool_call"):
            run.tool_calls = [*(run.tool_calls or []), normalized_event["tool_call"]]
            update_fields.append("tool_calls")
        if normalized_event.get("error"):
            run.error = normalized_event["error"]
            update_fields.append("error")
    await run.save(update_fields=sorted(set(update_fields)))
    return run


async def record_runtime_event(
    task: CommandTask,
    run: CommandTaskRun,
    event_type: str,
    payload: Any,
    operator_id: str | None = None,
) -> dict:
    from services.command_center.manager import record_task_event

    normalized = normalize_runtime_event(event_type, payload)
    await heartbeat_run(run, normalized)
    task_status = normalized.get("task_status")
    if task_status and task.status != task_status:
        task.status = task_status
        await task.save(update_fields=["status", "utime"])
    await record_task_event(
        task,
        normalized["event_type"],
        normalized.get("message"),
        operator_id,
        {"run_id": run.run_id, **(normalized.get("metadata") or {})},
    )
    if normalized["event_type"] == "approval_required":
        from services.command_center.approval_service import create_approval_request

        requested_action = normalized.get("requested_action") or {}
        await create_approval_request(
            task,
            run,
            trigger_type="runtime",
            requested_action=requested_action,
            risk_level=requested_action.get("risk_level") or "medium",
            operator_id=operator_id,
            reason="数字员工请求人工审批",
        )
    return normalized


async def finalize_run(
    run: CommandTaskRun,
    status: str,
    *,
    result: dict | None = None,
    error: dict | None = None,
    usage: dict | None = None,
    evidence_refs: list[str] | None = None,
) -> CommandTaskRun:
    if status not in TERMINAL_RUN_STATUSES:
        raise ValueError(f"invalid terminal run status: {status}")
    await run.refresh_from_db(fields=["metadata"])
    run.status = status
    run.finished_at = _now()
    run.heartbeat_at = run.finished_at
    if result is not None:
        metadata = run.metadata if isinstance(run.metadata, dict) else {}
        run.metadata = {**metadata, "result": result}
    if error is not None:
        run.error = error
    if usage is not None:
        run.usage = usage
    if evidence_refs is not None:
        run.evidence_refs = [*(run.evidence_refs or []), *evidence_refs]
    await run.save(update_fields=["status", "finished_at", "heartbeat_at", "metadata", "error", "usage", "evidence_refs", "utime"])
    return run


async def execute_task_run(
    task: CommandTask,
    employee: CommandAgent,
    operator_id: str | None = None,
    lease_owner: str | None = None,
    *,
    continuation_approval_id: str | None = None,
    continued_from_run_id: str | None = None,
    skip_pre_run_policy: bool = False,
    approved_policy_mutation: str | None = None,
    on_run_checked_out: RunCheckedOutHandler | None = None,
) -> dict:
    continuation_workspace = await _continuation_workspace(employee, approved_policy_mutation, continuation_approval_id)
    continuation_metadata = None
    if continuation_approval_id:
        continuation_metadata = {
            "continuation_approval_id": continuation_approval_id,
            "continued_from_run_id": continued_from_run_id,
            "skip_pre_run_policy": skip_pre_run_policy,
            "approved_policy_mutation": approved_policy_mutation,
        }
        if continuation_workspace:
            continuation_metadata["workspace_id"] = continuation_workspace["workspace_id"]
    run = await checkout_run(
        task,
        employee,
        operator_id=operator_id,
        lease_owner=lease_owner,
        metadata=continuation_metadata,
        policy_snapshot_override=continuation_workspace["policy_snapshot"] if continuation_workspace else None,
        workspace_etag_override=continuation_workspace["workspace_etag"] if continuation_workspace else None,
    )
    if on_run_checked_out:
        await on_run_checked_out(run)
    if continuation_approval_id:
        from services.command_center.manager import record_task_event

        await record_task_event(
            task,
            "continuation_started",
            "审批已通过，重新启动数字员工运行",
            operator_id,
            {
                "approval_id": continuation_approval_id,
                "run_id": run.run_id,
                "continued_from_run_id": continued_from_run_id,
                "policy_mutation": approved_policy_mutation,
            },
        )
    from services.command_center.approval_service import enforce_pre_run_policy

    if not skip_pre_run_policy:
        policy_result = await enforce_pre_run_policy(task, run, operator_id=operator_id)
        if policy_result["decision"] != "allow":
            return {"content": "", "policy": policy_result["decision"], "run_id": run.run_id}

    async def _on_runtime_event(event_type: str, payload: dict) -> dict | None:
        return await record_runtime_event(task, run, event_type, payload, operator_id)

    try:
        from services.digital_employee.execute_coordinator import run as execute_run

        result = await execute_run(
            task,
            employee,
            operator_id=operator_id,
            command_run=run,
            on_runtime_event=_on_runtime_event,
            continuation_workspace=continuation_workspace,
        )
        result = {**(result or {}), "run_id": run.run_id}
        if result.get("policy") == "approval_required":
            return result
        if result.get("policy") == "policy_denied":
            runtime_event = result.get("runtime_event") or {}
            await finalize_run(
                run,
                COMMAND_RUN_STATUS_FAILED,
                error=runtime_event.get("error") or {"message": "policy denied"},
                usage=result.get("usage"),
            )
            from services.command_center.manager import update_task_progress

            await update_task_progress(
                task,
                {
                    "status": COMMAND_TASK_STATUS_FAILED,
                    "message": runtime_event.get("message") or "策略拒绝本次运行",
                    "metadata": {"run_id": run.run_id, "runtime_event": runtime_event},
                },
                operator_id,
            )
            return result
        await finalize_run(run, COMMAND_RUN_STATUS_COMPLETED, result=result, usage=result.get("usage"))
        if task.status != COMMAND_TASK_STATUS_COMPLETED:
            from services.command_center.manager import update_task_progress

            await update_task_progress(
                task,
                {"status": COMMAND_TASK_STATUS_COMPLETED, "progress": 100, "message": "数字员工执行完成", "metadata": {"run_id": run.run_id}},
                operator_id,
            )
        return result
    except Exception as exc:
        await finalize_run(run, COMMAND_RUN_STATUS_FAILED, error={"message": str(exc)})
        raise


async def cancel_run(run: CommandTaskRun, operator_id: str | None = None, reason: str | None = None) -> CommandTaskRun:
    from services.command_center.manager import record_task_event, update_task_progress

    task = await run.task
    await finalize_run(run, COMMAND_RUN_STATUS_CANCELLED, error={"message": reason or "cancelled"})
    await update_task_progress(task, {"status": COMMAND_TASK_STATUS_CANCELLED, "message": reason or "运行已取消"}, operator_id)
    await record_task_event(task, "run_cancelled", reason or "运行已取消", operator_id, {"run_id": run.run_id})
    return run


async def handoff_run(run: CommandTaskRun, operator_id: str | None = None, reason: str | None = None) -> CommandTaskRun:
    from services.command_center.manager import record_task_event, update_task_progress

    task = await run.task
    await finalize_run(run, COMMAND_RUN_STATUS_HANDED_OFF, error={"message": reason or "handed off"})
    await update_task_progress(task, {"status": COMMAND_TASK_STATUS_HANDED_OFF, "message": reason or "运行已人工接管"}, operator_id)
    await record_task_event(task, "run_handed_off", reason or "运行已人工接管", operator_id, {"run_id": run.run_id})
    return run


async def retry_run(run: CommandTaskRun, operator_id: str | None = None) -> dict:
    task = await run.task
    employee = await run.employee
    if employee is None:
        raise ValueError("cannot retry run without employee")
    task.status = COMMAND_TASK_STATUS_DISPATCHED
    task.progress = 0
    task.run_id = None
    await task.save(update_fields=["status", "progress", "run_id", "utime"])
    return await execute_task_run(task, employee, operator_id=operator_id)


async def recover_stale_runs(tenant_id: str | None = None, now: datetime | None = None) -> list[CommandTaskRun]:
    from services.command_center.manager import record_task_event, update_task_progress

    current = now or _now()
    query = CommandTaskRun.filter(status__in=ACTIVE_RUN_STATUSES, lease_expires_at__lt=current)
    if tenant_id:
        query = query.filter(tenant_id=tenant_id)
    stale_runs = await query.all()
    recovered: list[CommandTaskRun] = []
    for run in stale_runs:
        task = await run.task
        await finalize_run(run, COMMAND_RUN_STATUS_STALE, error={"message": "run lease expired"})
        await update_task_progress(task, {"status": COMMAND_TASK_STATUS_FAILED, "message": "数字员工运行租约过期", "metadata": {"run_id": run.run_id}}, None)
        await record_task_event(task, "run_stale_recovered", "运行租约过期，已标记 stale", None, {"run_id": run.run_id})
        recovered.append(run)
    return recovered
