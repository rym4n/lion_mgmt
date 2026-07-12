# coding=utf-8
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sanic.log import logger
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.command_center import (
    COMMAND_APPROVAL_STATUS_APPROVED,
    COMMAND_APPROVAL_STATUS_APPROVED_READ_ONLY,
    COMMAND_APPROVAL_STATUS_PENDING,
    COMMAND_RUN_STATUS_CHECKED_OUT,
    COMMAND_RUN_STATUS_FAILED,
    COMMAND_RUN_STATUS_RUNNING,
    COMMAND_RUN_STATUS_WAITING_APPROVAL,
    COMMAND_TASK_STATUS_DISPATCHED,
    COMMAND_TASK_STATUS_FAILED,
    COMMAND_TASK_STATUS_PENDING_APPROVAL,
    COMMAND_TASK_STATUS_RUNNING,
    COMMAND_TASK_TERMINAL_STATUSES,
    CommandApproval,
    CommandTask,
    CommandTaskRun,
)
from settings import TZ

from .approval_contract import (
    APPROVEMENT_PRESENTATION_ADAPTER,
    approval_decision_transition,
    approval_required_context,
)


POLICY_ALLOW = "allow"
POLICY_DENY = "deny"
POLICY_APPROVAL_REQUIRED = "approval_required"
DECISION_ALIASES = {
    "approve": "approve",
    "approved": "approve",
    "reject": "reject",
    "rejected": "reject",
    "needs_info": "needs_info",
    "read_only": "read_only",
    "approved_read_only": "read_only",
    "takeover": "takeover",
    "handed_off": "takeover",
}
RESUMABLE_APPROVAL_STATUSES = {COMMAND_APPROVAL_STATUS_APPROVED, COMMAND_APPROVAL_STATUS_APPROVED_READ_ONLY}
RECOVERY_CLAIM_TTL_SECONDS = 300
AGENT_APPROVAL_KIND = "agent"


@dataclass(slots=True)
class PolicyDecision:
    decision: str
    risk_level: str
    reasons: list[str]
    requested_action: dict[str, Any]
    trigger_type: str

    @property
    def requires_approval(self) -> bool:
        return self.decision == POLICY_APPROVAL_REQUIRED

    @property
    def denied(self) -> bool:
        return self.decision == POLICY_DENY


def _policy_value(policy: dict, *keys: str) -> Any:
    current: Any = policy
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _normalize_policy_value(value: Any) -> str:
    raw = str(value or "").lower()
    if raw in {"deny", "denied", "forbid", "forbidden"}:
        return POLICY_DENY
    if raw in {"approval_required", "required", "manual", "manual_approval"}:
        return POLICY_APPROVAL_REQUIRED
    return POLICY_ALLOW


def _impacted_resources(policy_snapshot: dict | None) -> list[dict]:
    resource_scope = _policy_value(policy_snapshot or {}, "resource_scope")
    if not resource_scope:
        return []
    if isinstance(resource_scope, list):
        return resource_scope
    return [{"kind": "resource_scope", "scope": resource_scope}]


def _approval_title(task: CommandTask) -> str:
    content = getattr(task, "description", None) or getattr(task, "title", None) or task.id
    compact_content = " ".join(str(content).split())
    title = f"Agent 审批：{compact_content}"
    return title if len(title) <= 50 else f"{title[:47]}..."


def _approval_content(task: CommandTask, run: CommandTaskRun, requested_action: dict | None, reason: str | None) -> str:
    action = requested_action or {}
    action_name = action.get("tool") or action.get("playbook") or action.get("action") or action.get("name") or "执行动作"
    task_content = getattr(task, "description", None) or getattr(task, "title", None) or task.id
    lines = [
        reason or "数字员工任务需要人工审批",
        f"任务内容：{task_content}",
        f"运行 ID：{run.run_id}",
        f"动作：{action_name}",
    ]
    return "\n".join(lines)


def _command_approval_params(approval: CommandApproval, task: CommandTask, run: CommandTaskRun) -> dict:
    return {
        "approval_kind": AGENT_APPROVAL_KIND,
        "command_approval_id": approval.id,
        "command_task_id": task.id,
        "command_run_id": run.run_id,
    }


def _has_approval_permission(account: Any) -> bool:
    from libs.security.authorization import check_wildcard

    for role in getattr(account, "roles", []) or []:
        for permission in getattr(role, "permissions", []) or []:
            if check_wildcard(str(permission), "approve:u"):
                return True
    return False


async def _agent_approval_approvers(tenant_id: str, operator_id: str | None) -> list[Any]:
    try:
        from models import Account
    except ImportError:
        return []

    accounts = await Account.filter(deleted=False, disabled=False, type="user", member__id=tenant_id).prefetch_related("roles").all()
    approvers = [account for account in accounts if _has_approval_permission(account)]
    if approvers or not operator_id:
        return approvers
    requester = await Account.get_or_none(id=operator_id, deleted=False)
    return [requester] if requester else []


async def sync_approvement_for_command_approval(
    approval: CommandApproval,
    task: CommandTask,
    run: CommandTaskRun,
    *,
    operator_id: str | None = None,
    reason: str | None = None,
) -> None:
    try:
        from models import Approvement
    except ImportError:
        return

    metadata = dict(approval.metadata or {})
    if metadata.get("approvement_id"):
        return
    now = datetime.now(TZ)
    approvement = await Approvement.create(
        tenant_id=task.tenant_id,
        title=_approval_title(task),
        flow_id=None,
        run_id=run.run_id,
        content=_approval_content(task, run, approval.requested_action, reason),
        params=_command_approval_params(approval, task, run),
        status=0,
        expire_time=getattr(approval, "expires_at", None) or now + timedelta(hours=24),
        created_at=now,
        audit_node=approval.id,
        approve_type=1,
        callback_token=f"agent-{uuid4().hex}",
    )
    approvers = await _agent_approval_approvers(task.tenant_id, operator_id)
    if approvers:
        await approvement.approvers.add(*approvers)
    approval.metadata = {
        **metadata,
        "visible_inbox": "approvement",
        "source": "command_approval",
        "approval_kind": AGENT_APPROVAL_KIND,
        "approvement_id": approvement.id,
    }
    await approval.save(update_fields=["metadata", "utime"])


async def sync_command_approvement_decision(
    approval: CommandApproval,
    decision: str,
    approver_id: str,
    comment: str | None = None,
) -> None:
    try:
        from models import Account, Approvement
    except ImportError:
        return

    approvement_id = (approval.metadata or {}).get("approvement_id")
    if not approvement_id:
        return
    approvement = await Approvement.filter(
        id=approvement_id,
        tenant_id=approval.tenant_id,
        deleted=False,
    ).prefetch_related("approvers").first()
    if not approvement:
        return
    account = await Account.get_or_none(id=approver_id, deleted=False)
    agree = decision in {"approve", "approved", "read_only", "takeover"}
    if decision == "needs_info":
        status = 0
    else:
        status = 1 if agree else 2
    approvement.status = status
    approvement.results = {
        **(approvement.results or {}),
        approver_id: {
            "username": getattr(account, "username", approver_id),
            "display_name": getattr(account, "display_name", None) or getattr(account, "username", approver_id),
            "agree": agree,
            "remark": comment or "",
            "approve_time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    await approvement.save(update_fields=["status", "results", "utime"])


def evaluate_policy(policy_snapshot: dict | None, requested_action: dict | None = None, trigger_type: str = "pre_run") -> PolicyDecision:
    policy = policy_snapshot or {}
    action = requested_action or {}
    approval_policy = policy.get("approval") if isinstance(policy.get("approval"), dict) else {}
    tool_name = action.get("tool") or action.get("tool_name") or action.get("skill_key")
    reasons: list[str] = []

    explicit = action.get("policy_decision") or action.get("decision")
    if explicit:
        decision = _normalize_policy_value(explicit)
        reasons.append(f"requested_action.{explicit}")
    elif tool_name and tool_name in (approval_policy.get("skills") or {}):
        decision = _normalize_policy_value((approval_policy.get("skills") or {}).get(tool_name))
        reasons.append(f"approval.skills.{tool_name}")
    else:
        decision = _normalize_policy_value(approval_policy.get("default"))
        reasons.append("approval.default")

    risk_level = action.get("risk_level") or approval_policy.get("risk_level") or ("high" if decision != POLICY_ALLOW else "medium")
    return PolicyDecision(
        decision=decision,
        risk_level=str(risk_level),
        reasons=reasons,
        requested_action=action,
        trigger_type=trigger_type,
    )


async def create_approval_request(
    task: CommandTask,
    run: CommandTaskRun,
    *,
    trigger_type: str,
    requested_action: dict | None = None,
    risk_level: str = "medium",
    operator_id: str | None = None,
    reason: str | None = None,
) -> CommandApproval:
    from services.command_center.manager import record_task_event

    async with in_transaction() as connection:
        locked_run = (
            await CommandTaskRun.filter(id=run.id, tenant_id=task.tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not locked_run:
            raise CustomError(ErrorCode.NOT_FOUND)
        locked_task = (
            await CommandTask.filter(id=task.id, tenant_id=task.tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not locked_task:
            raise CustomError(ErrorCode.NOT_FOUND)
        if (
            locked_run.status
            not in {
                COMMAND_RUN_STATUS_CHECKED_OUT,
                COMMAND_RUN_STATUS_RUNNING,
                COMMAND_RUN_STATUS_WAITING_APPROVAL,
            }
            or locked_task.status
            not in {
                COMMAND_TASK_STATUS_DISPATCHED,
                COMMAND_TASK_STATUS_RUNNING,
                COMMAND_TASK_STATUS_PENDING_APPROVAL,
            }
            or locked_task.run_id != locked_run.run_id
        ):
            raise CustomError(ErrorCode.APPROVEMENT_STATUS_ERROR)

        employee = await locked_run.employee
        approval = await CommandApproval.create(
            using_db=connection,
            tenant_id=locked_task.tenant_id,
            task=locked_task,
            run=locked_run,
            employee=employee,
            status=COMMAND_APPROVAL_STATUS_PENDING,
            trigger_type=trigger_type,
            risk_level=risk_level,
            policy_snapshot=locked_run.policy_snapshot or {},
            impacted_resources=_impacted_resources(locked_run.policy_snapshot or {}),
            requested_action=requested_action or {},
            requester_id=operator_id,
            metadata={
                "visible_inbox": APPROVEMENT_PRESENTATION_ADAPTER,
                "source": "command_approval",
                "reason": reason,
                "run_id": locked_run.run_id,
            },
        )
        await sync_approvement_for_command_approval(
            approval,
            locked_task,
            locked_run,
            operator_id=operator_id,
            reason=reason,
        )
        locked_run.status = COMMAND_RUN_STATUS_WAITING_APPROVAL
        await locked_run.save(using_db=connection, update_fields=["status", "utime"])
        locked_task.status = COMMAND_TASK_STATUS_PENDING_APPROVAL
        await locked_task.save(using_db=connection, update_fields=["status", "utime"])
        await record_task_event(
            locked_task,
            "approval_required",
            reason or "任务需要人工审批",
            operator_id,
            {
                "run_id": locked_run.run_id,
                "approval_id": approval.id,
                "trigger_type": trigger_type,
                "risk_level": risk_level,
            },
            using_db=connection,
        )
    run.status = locked_run.status
    task.status = locked_task.status
    return approval


async def enforce_pre_run_policy(task: CommandTask, run: CommandTaskRun, operator_id: str | None = None) -> dict:
    from services.command_center.manager import record_task_event

    decision = evaluate_policy(
        run.policy_snapshot or {},
        {
            "task_id": task.id,
            "run_id": run.run_id,
            "trigger_type": "pre_run",
        },
        trigger_type="pre_run",
    )
    if decision.denied:
        from services.command_center.run_manager import finalize_run_and_task

        await finalize_run_and_task(
            run,
            task,
            COMMAND_RUN_STATUS_FAILED,
            {
                "status": COMMAND_TASK_STATUS_FAILED,
                "message": "策略拒绝任务执行",
                "metadata": {"run_id": run.run_id, "reasons": decision.reasons},
            },
            operator_id=operator_id,
            error={"message": "policy denied", "reasons": decision.reasons},
        )
        await record_task_event(task, "policy_denied", "策略拒绝任务执行", operator_id, {"run_id": run.run_id, "reasons": decision.reasons})
        return {"decision": decision.decision, "policy_decision": decision}
    if decision.requires_approval:
        approval = await create_approval_request(
            task,
            run,
            trigger_type="pre_run",
            requested_action=decision.requested_action,
            risk_level=decision.risk_level,
            operator_id=operator_id,
            reason="策略要求执行前审批",
        )
        return {"decision": decision.decision, "policy_decision": decision, "approval": approval}
    return {"decision": decision.decision, "policy_decision": decision}


async def list_approvals(tenant_id: str, filters: dict) -> tuple[list[CommandApproval], int]:
    condition = {"tenant_id": tenant_id, "deleted": False}
    if filters.get("status"):
        condition["status"] = filters["status"]
    if filters.get("task_id"):
        condition["task_id"] = filters["task_id"]
    if filters.get("run_id"):
        condition["run_id"] = filters["run_id"]
    page = max(1, int(filters.get("page", 1)))
    size = max(1, min(int(filters.get("size", 20)), 100))
    offset = (page - 1) * size
    query = CommandApproval.filter(**condition)
    total = await query.count()
    records = await query.order_by("-ctime").offset(offset).limit(size).all()
    return records, total


async def get_approval(tenant_id: str, approval_id: str) -> CommandApproval:
    approval = await CommandApproval.get_or_none(id=approval_id, tenant_id=tenant_id, deleted=False)
    if not approval:
        raise CustomError(ErrorCode.NOT_FOUND)
    return approval


def schedule_approval_resume(tenant_id: str, approval_id: str, operator_id: str | None = None) -> None:
    async def _runner() -> None:
        try:
            await resume_approved_approval(tenant_id, approval_id, operator_id=operator_id)
        except Exception as exc:
            logger.warning(f"指挥中心审批继续执行失败: approval_id={approval_id}, error={exc}")

    asyncio.create_task(_runner())


def schedule_approvement_decision_sync(
    approval: CommandApproval,
    decision: str,
    approver_id: str,
    comment: str | None = None,
) -> None:
    async def _runner() -> None:
        try:
            await sync_command_approvement_decision(approval, decision, approver_id, comment)
        except Exception as exc:
            logger.warning(f"指挥中心审批待办同步失败: approval_id={approval.id}, error={exc}")

    asyncio.create_task(_runner())


def _needs_continuation_resume(approval: CommandApproval) -> bool:
    metadata = approval.metadata or {}
    recovery = metadata.get("continuation_recovery")
    return not metadata.get("continuation_run_id") and not (
        isinstance(recovery, dict) and recovery.get("status") == "not_applicable"
    )


def _run_matches_continuation_approval(run: CommandTaskRun, approval_id: str) -> bool:
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    return metadata.get("continuation_approval_id") == approval_id


async def _existing_continuation_run(approval: CommandApproval, task: CommandTask | None = None) -> CommandTaskRun | None:
    task_id = getattr(task, "id", None) or getattr(approval, "task_id", None)
    if not task_id:
        return None
    runs = await CommandTaskRun.filter(tenant_id=approval.tenant_id, task_id=task_id).all()
    for run in runs:
        if _run_matches_continuation_approval(run, approval.id):
            return run
    return None


def _resume_in_progress_result(approval: CommandApproval, metadata: dict) -> dict:
    recovery = metadata.get("continuation_recovery") if isinstance(metadata.get("continuation_recovery"), dict) else {}
    return {
        "policy": "resume_in_progress",
        "approval_id": approval.id,
        "run_id": metadata.get("continuation_run_id"),
        "claimed_at": recovery.get("claimed_at"),
    }


def _resume_not_applicable_result(approval: CommandApproval, metadata: dict) -> dict:
    recovery = metadata.get("continuation_recovery") if isinstance(metadata.get("continuation_recovery"), dict) else {}
    return {
        "policy": "resume_not_applicable",
        "approval_id": approval.id,
        "run_id": None,
        "task_status": recovery.get("task_status"),
    }


async def _mark_continuation_run(
    approval: CommandApproval,
    run: CommandTaskRun,
    *,
    result_policy: str = "already_continued",
    recovery_status: str = "succeeded",
) -> dict:
    metadata = {
        **(approval.metadata or {}),
        "continuation_run_id": run.run_id,
        "continuation_result_policy": result_policy,
    }
    recovery = metadata.get("continuation_recovery")
    if isinstance(recovery, dict):
        recovery_update = {**recovery, "status": recovery_status, "run_id": run.run_id}
        if recovery_status == "succeeded":
            recovery_update["completed_at"] = datetime.now(TZ).isoformat()
        metadata["continuation_recovery"] = recovery_update
    approval.metadata = metadata
    await approval.save(update_fields=["metadata", "utime"])
    return {"policy": result_policy, "run_id": run.run_id, "approval_id": approval.id}


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _active_recovery_claim(metadata: dict, now: datetime) -> bool:
    recovery = metadata.get("continuation_recovery")
    if not isinstance(recovery, dict) or recovery.get("status") != "running":
        return False
    claimed_at = _parse_datetime(recovery.get("claimed_at"))
    return bool(claimed_at and now - claimed_at < timedelta(seconds=RECOVERY_CLAIM_TTL_SECONDS))


def approval_resume_claim_is_active(approval: CommandApproval, now: datetime | None = None) -> bool:
    return (
        approval.status in RESUMABLE_APPROVAL_STATUSES
        and _needs_continuation_resume(approval)
        and _active_recovery_claim(approval.metadata or {}, now or datetime.now(TZ))
    )


async def _claim_approval_resume(
    approval: CommandApproval,
    operator_id: str | None,
) -> tuple[CommandApproval | None, dict | None]:
    """Resolve or claim a continuation while holding its approval row lock."""
    async with in_transaction() as connection:
        locked = (
            await CommandApproval.filter(id=approval.id, tenant_id=approval.tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not locked or locked.status not in RESUMABLE_APPROVAL_STATUSES:
            return None, None
        metadata = dict(locked.metadata or {})
        if metadata.get("continuation_run_id"):
            return None, {
                "policy": "already_continued",
                "run_id": metadata["continuation_run_id"],
                "approval_id": locked.id,
            }
        if not _needs_continuation_resume(locked):
            return None, _resume_not_applicable_result(locked, metadata)

        task_ref = await locked.task
        if task_ref is None:
            return None, None
        task = (
            await CommandTask.filter(id=task_ref.id, tenant_id=locked.tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if task is None:
            return None, None
        existing_run = await _existing_continuation_run(locked, task)
        if existing_run:
            return None, await _mark_continuation_run(locked, existing_run)

        now = datetime.now(TZ)
        if task.status in COMMAND_TASK_TERMINAL_STATUSES:
            recovery = metadata.get("continuation_recovery")
            locked.metadata = {
                **metadata,
                "continuation_recovery": {
                    **(recovery if isinstance(recovery, dict) else {}),
                    "status": "not_applicable",
                    "reason": "task_terminal",
                    "task_status": task.status,
                    "skipped_at": now.isoformat(),
                },
            }
            await locked.save(using_db=connection, update_fields=["metadata", "utime"])
            approval.metadata = locked.metadata
            return None, _resume_not_applicable_result(locked, locked.metadata)
        if _active_recovery_claim(metadata, now):
            return None, _resume_in_progress_result(locked, metadata)
        metadata["continuation_recovery"] = {"status": "running", "claimed_at": now.isoformat(), "operator_id": operator_id}
        locked.metadata = metadata
        await locked.save(using_db=connection, update_fields=["metadata", "utime"])
        return locked, None


async def recover_pending_approval_resumes(tenant_id: str | None = None, operator_id: str | None = None) -> list[dict]:
    query = CommandApproval.filter(status__in=RESUMABLE_APPROVAL_STATUSES, deleted=False)
    if tenant_id:
        query = query.filter(tenant_id=tenant_id)
    approvals = await query.all()
    scheduled: list[dict] = []
    for approval in approvals:
        if not _needs_continuation_resume(approval) or approval_resume_claim_is_active(approval):
            continue
        schedule_approval_resume(approval.tenant_id, approval.id, operator_id=operator_id)
        scheduled.append({"policy": "resume_scheduled", "approval_id": approval.id})
    return scheduled


async def resume_approved_approval(tenant_id: str, approval_id: str, operator_id: str | None = None) -> dict:
    approval = await CommandApproval.get_or_none(id=approval_id, tenant_id=tenant_id, deleted=False)
    if not approval:
        raise CustomError(ErrorCode.NOT_FOUND)
    if approval.status not in RESUMABLE_APPROVAL_STATUSES:
        raise CustomError(ErrorCode.APPROVEMENT_STATUS_ERROR)

    metadata = dict(approval.metadata or {})
    if metadata.get("continuation_run_id"):
        return {"policy": "already_continued", "run_id": metadata["continuation_run_id"], "approval_id": approval.id}
    if not _needs_continuation_resume(approval):
        return _resume_not_applicable_result(approval, metadata)
    claimed, resolved = await _claim_approval_resume(approval, operator_id)
    if resolved is not None:
        return resolved
    if not claimed:
        current = await CommandApproval.get_or_none(id=approval_id, tenant_id=tenant_id, deleted=False)
        if not current:
            raise CustomError(ErrorCode.NOT_FOUND)
        current_metadata = dict(current.metadata or {})
        if current_metadata.get("continuation_run_id"):
            return {"policy": "already_continued", "run_id": current_metadata["continuation_run_id"], "approval_id": current.id}
        if not _needs_continuation_resume(current):
            return _resume_not_applicable_result(current, current_metadata)
        existing_run = await _existing_continuation_run(current)
        if existing_run:
            return await _mark_continuation_run(current, existing_run)
        if _active_recovery_claim(current_metadata, datetime.now(TZ)):
            return _resume_in_progress_result(current, current_metadata)
        raise CustomError(ErrorCode.APPROVEMENT_STATUS_ERROR)

    approval = claimed
    metadata = dict(approval.metadata or {})
    task = await approval.task
    previous_run = await approval.run
    employee = await approval.employee
    if employee is None and previous_run is not None:
        employee = await previous_run.employee
    if task is None or previous_run is None or employee is None:
        raise CustomError(ErrorCode.NOT_FOUND)
    existing_run = await _existing_continuation_run(approval, task)
    if existing_run:
        return await _mark_continuation_run(approval, existing_run)

    from services.command_center.manager import record_task_event
    from services.command_center.run_manager import execute_task_run

    transition = metadata.get("transition") or {}

    async def _mark_run_checked_out(run: CommandTaskRun) -> None:
        await _mark_continuation_run(approval, run, result_policy="running", recovery_status="running")

    try:
        result = await execute_task_run(
            task,
            employee,
            operator_id=operator_id,
            lease_owner=f"approval:{approval.id}",
            continuation_approval_id=approval.id,
            continued_from_run_id=previous_run.run_id,
            skip_pre_run_policy=True,
            approved_policy_mutation=transition.get("policy_mutation"),
            on_run_checked_out=_mark_run_checked_out,
        )
    except Exception as exc:
        failure_metadata = dict(approval.metadata or metadata)
        recovery = failure_metadata.get("continuation_recovery")
        if isinstance(recovery, dict):
            failure_metadata["continuation_recovery"] = {
                **recovery,
                "status": "failed",
                "error": str(exc),
                "failed_at": datetime.now(TZ).isoformat(),
            }
            approval.metadata = failure_metadata
            await approval.save(update_fields=["metadata", "utime"])
        await record_task_event(
            task,
            "continuation_failed",
            f"审批通过后的继续执行失败: {exc}",
            operator_id,
            {"approval_id": approval.id, "run_id": previous_run.run_id, "error": str(exc)},
        )
        raise

    metadata = dict(approval.metadata or metadata)
    recovery = metadata.get("continuation_recovery")
    if isinstance(recovery, dict):
        recovery = {**recovery, "status": "succeeded", "completed_at": datetime.now(TZ).isoformat()}
    approval.metadata = {
        **metadata,
        "continuation_run_id": result.get("run_id") or metadata.get("continuation_run_id"),
        "continuation_result_policy": result.get("policy") or "completed",
    }
    if recovery:
        approval.metadata["continuation_recovery"] = recovery
    await approval.save(update_fields=["metadata", "utime"])
    return result


def _approval_context(approval: CommandApproval, task: CommandTask, run: CommandTaskRun) -> dict:
    return approval_required_context(
        tenant_id=approval.tenant_id,
        task_id=task.id,
        run_id=run.run_id,
        approval_id=approval.id,
        employee_id=approval.employee_id,
        session_id=run.session_id or run.run_id,
        policy_snapshot=approval.policy_snapshot or run.policy_snapshot or {},
        requested_action=approval.requested_action or {},
        evidence_refs=run.evidence_refs or [],
        risk_level=approval.risk_level,
        trigger_type=approval.trigger_type,
        resource_scope=_policy_value(approval.policy_snapshot or {}, "resource_scope") or {},
    )


async def decide_approval(
    tenant_id: str,
    approval_id: str,
    decision: str,
    approver_id: str,
    comment: str | None = None,
) -> CommandApproval:
    from services.command_center.manager import record_task_event

    transition_key = DECISION_ALIASES.get(decision)
    if not transition_key:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

    from services.command_center.manager import (
        apply_task_progress_in_transaction,
        complete_task_progress_after_commit,
    )

    should_resume = False
    task_transition = None
    async with in_transaction() as connection:
        approval = (
            await CommandApproval.filter(id=approval_id, tenant_id=tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not approval:
            raise CustomError(ErrorCode.NOT_FOUND)
        if approval.status != COMMAND_APPROVAL_STATUS_PENDING:
            raise CustomError(ErrorCode.APPROVEMENT_STATUS_ERROR)
        task_ref = await approval.task
        run_ref = await approval.run
        if task_ref is None or run_ref is None:
            raise CustomError(ErrorCode.NOT_FOUND)
        run = (
            await CommandTaskRun.filter(id=run_ref.id, tenant_id=tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        task = (
            await CommandTask.filter(id=task_ref.id, tenant_id=tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not run or not task:
            raise CustomError(ErrorCode.NOT_FOUND)
        if (
            run.status != COMMAND_RUN_STATUS_WAITING_APPROVAL
            or task.status != COMMAND_TASK_STATUS_PENDING_APPROVAL
            or task.run_id != run.run_id
        ):
            raise CustomError(ErrorCode.APPROVEMENT_STATUS_ERROR)
        context = _approval_context(approval, task, run)
        transition = approval_decision_transition(transition_key, context)

        now = datetime.now(TZ)
        approval.status = transition["approval_status"]
        approval.approver_id = approver_id
        approval.decided_at = now
        approval.decision_comment = comment
        metadata = {
            **(approval.metadata or {}),
            "decision": transition_key,
            "transition": transition,
            "visible_inbox": APPROVEMENT_PRESENTATION_ADAPTER,
        }
        if transition.get("needs_info"):
            metadata["needs_info"] = {"requested": True, "comment": comment, "requested_by": approver_id, "requested_at": now.isoformat()}
        approval.metadata = metadata
        await approval.save(
            using_db=connection,
            update_fields=["status", "approver_id", "decided_at", "decision_comment", "metadata", "utime"],
        )

        run.status = transition["run_status"]
        if transition.get("release_lease"):
            run.lease_owner = None
            run.lease_expires_at = now
        run.metadata = {**(run.metadata or {}), "approval_decision": transition}
        await run.save(
            using_db=connection,
            update_fields=["status", "lease_owner", "lease_expires_at", "metadata", "utime"],
        )

        if transition["task_status"] in {"cancelled", "handed_off", "failed"}:
            task_transition = await apply_task_progress_in_transaction(
                connection,
                task,
                {
                    "status": transition["task_status"],
                    "message": comment or "审批已决策",
                    "metadata": {"approval_id": approval.id, "run_id": run.run_id},
                },
                approver_id,
                locked_task=task,
                expected_run_id=run.run_id,
            )
        else:
            task.status = transition["task_status"]
            await task.save(using_db=connection, update_fields=["status", "utime"])

        await record_task_event(
            task,
            transition["event_type"],
            comment or "审批已决策",
            approver_id,
            {"approval_id": approval.id, "run_id": run.run_id, "decision": transition_key, "transition": transition},
            using_db=connection,
        )
        should_resume = bool(transition.get("next_event_type"))
    if task_transition is not None:
        await complete_task_progress_after_commit(
            task,
            task_transition,
            {"metadata": {"approval_id": approval.id, "run_id": run.run_id}},
        )
    if should_resume:
        schedule_approval_resume(tenant_id, approval_id, operator_id=approver_id)
    # Approvement is only a presentation adapter. Keep it off the durable
    # decision and continuation path so a slow inbox cannot block either one.
    schedule_approvement_decision_sync(approval, transition_key, approver_id, comment)
    return approval
