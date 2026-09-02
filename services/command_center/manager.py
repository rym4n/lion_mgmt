# coding=utf-8
import asyncio
from datetime import datetime

from sanic.log import logger
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from models.command_center import COMMAND_TASK_STATUS_PENDING_CLARIFICATION, CommandAgent, CommandTask, CommandTaskEvent
from settings import TZ

from .dispatcher import MAX_CLARIFICATION_QUESTION_LENGTH, choose_agent_with_lion_agent
from .realtime import publish_command_center_update


TERMINAL_TASK_STATUS = {"completed", "failed", "cancelled", "handed_off"}
DISPATCHABLE_TASK_STATUS = {"pending_dispatch"}
DEFAULT_COORDINATOR_AGENT_KEY = "command_center_coordinator"
DEFAULT_COORDINATOR_AGENT_NAME = "指挥部分工协调智能体"
DEFAULT_COORDINATOR_CAPABILITIES = ["command_center", "coordination", "dispatch", "task_planning"]
DEFAULT_TASK_TITLE = "任务"
MAX_COMPAT_TASK_TITLE_LENGTH = 255
TASK_CONTENT_FIELDS = ("description", "content", "prompt", "message", "query", "question", "task")
MAX_CLARIFICATION_ANSWER_LENGTH = 4000
MAX_CLARIFICATION_HISTORY = 10


class InvalidClarificationAnswer(ValueError):
    pass


class TaskClarificationConflict(ValueError):
    pass


class TaskClarificationForbidden(PermissionError):
    pass


def _normalize_clarifications(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    clarifications = []
    for item in value[-MAX_CLARIFICATION_HISTORY:]:
        if not isinstance(item, dict):
            continue
        question = item.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        answer = item.get("answer")
        if answer is not None and not isinstance(answer, str):
            continue
        clarifications.append(
            {
                "question": " ".join(question.split())[:MAX_CLARIFICATION_QUESTION_LENGTH],
                "answer": answer.strip()[:MAX_CLARIFICATION_ANSWER_LENGTH] if isinstance(answer, str) else None,
            }
        )
    return clarifications


def _payload_with_clarification(payload, question: str) -> dict:
    normalized_payload = dict(payload) if isinstance(payload, dict) else {}
    clarifications = _normalize_clarifications(normalized_payload.get("clarifications"))
    clarifications.append({"question": question, "answer": None})
    normalized_payload["clarifications"] = clarifications[-MAX_CLARIFICATION_HISTORY:]
    return normalized_payload


def task_content_from_data(data: dict) -> str:
    """Return the original task content without summarizing or rewriting it."""
    for source in (data, data.get("payload")):
        if not isinstance(source, dict):
            continue
        for key in TASK_CONTENT_FIELDS:
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return str(data.get("title") or "").strip()


def has_task_content(data: dict) -> bool:
    return bool(task_content_from_data(data))


async def ensure_default_coordinator_agent(tenant_id: str) -> CommandAgent:
    """确保每个租户至少有一个默认的指挥部分工协调智能体。"""
    agent, _ = await CommandAgent.get_or_create(
        tenant_id=tenant_id,
        agent_key=DEFAULT_COORDINATOR_AGENT_KEY,
        defaults={
            "name": DEFAULT_COORDINATOR_AGENT_NAME,
            "description": "默认创建的指挥部分工协调智能体，负责理解任务、拆解分工并给出调度建议。",
            "capabilities": DEFAULT_COORDINATOR_CAPABILITIES,
            "status": "idle",
            "max_concurrent": 8,
            "metadata": {"runtime": "lion_agent", "role": "coordinator", "default": True},
            "runtime": "lion_agent",
            "prompt_config": {"source": "builtin"},
            "is_builtin": True,
        },
    )
    changed = False
    if agent.deleted:
        agent.deleted = False
        changed = True
    if agent.name != DEFAULT_COORDINATOR_AGENT_NAME:
        agent.name = DEFAULT_COORDINATOR_AGENT_NAME
        changed = True
    if agent.status in {"offline", "error"}:
        agent.status = "idle"
        changed = True
    merged_capabilities = sorted(set(agent.capabilities or []) | set(DEFAULT_COORDINATOR_CAPABILITIES))
    if agent.capabilities != merged_capabilities:
        agent.capabilities = merged_capabilities
        changed = True
    metadata = dict(agent.metadata or {})
    metadata.update({"runtime": "lion_agent", "role": "coordinator", "default": True})
    if agent.metadata != metadata:
        agent.metadata = metadata
        changed = True
    if agent.runtime != "lion_agent":
        agent.runtime = "lion_agent"
        changed = True
    prompt_config = dict(agent.prompt_config or {})
    prompt_config.update({"source": "builtin"})
    prompt_config.pop("instruction_format", None)
    if agent.prompt_config != prompt_config:
        agent.prompt_config = prompt_config
        changed = True
    if not agent.is_builtin:
        agent.is_builtin = True
        changed = True
    if changed:
        await agent.save()
    return agent


async def record_task_event(
    task: CommandTask,
    event_type: str,
    message: str | None = None,
    operator_id: str | None = None,
    metadata: dict | None = None,
    *,
    using_db=None,
) -> CommandTaskEvent:
    return await CommandTaskEvent.create(
        using_db=using_db,
        tenant_id=task.tenant_id,
        task=task,
        event_type=event_type,
        status=task.status,
        progress=task.progress,
        agent_key=task.assigned_agent_key,
        message=message,
        metadata=metadata or {},
        operator_id=operator_id,
    )


async def _release_agent_assignment(agent: CommandAgent | None, task_id: str, connection) -> None:
    if not agent:
        return
    agent.load = max(0, agent.load - 1)
    if agent.current_task_id == task_id:
        agent.current_task_id = None
    if agent.status == "busy" and agent.load < agent.max_concurrent:
        agent.status = "idle"
    await agent.save(
        using_db=connection,
        update_fields=["load", "current_task_id", "status"],
    )


async def dispatch_task(task: CommandTask, operator_id: str | None = None) -> CommandTask:
    if task.status not in DISPATCHABLE_TASK_STATUS:
        return task
    coordinator = await ensure_default_coordinator_agent(task.tenant_id)
    agents = await CommandAgent.filter(tenant_id=task.tenant_id, deleted=False, enabled=True).all()
    decision = await choose_agent_with_lion_agent(task, agents, coordinator)
    selected_agent = None

    async with in_transaction() as connection:
        locked_task = (
            await CommandTask.filter(id=task.id, tenant_id=task.tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not locked_task:
            return task
        if locked_task.status not in DISPATCHABLE_TASK_STATUS:
            return locked_task

        previous_agent_key = locked_task.assigned_agent_key
        decision_requests_clarification = getattr(decision, "needs_clarification", False) is True
        raw_question = getattr(decision, "clarification_question", None)
        clarification_question = " ".join(raw_question.split()) if isinstance(raw_question, str) else None
        clarification_requested = bool(
            decision_requests_clarification
            and clarification_question
            and len(clarification_question) <= MAX_CLARIFICATION_QUESTION_LENGTH
        )
        candidate_key = decision.agent.agent_key if decision.agent and not decision_requests_clarification else None
        agent_keys = sorted({key for key in (previous_agent_key, candidate_key) if key})
        locked_agents = []
        if agent_keys:
            locked_agents = (
                await CommandAgent.filter(
                    tenant_id=locked_task.tenant_id,
                    agent_key__in=agent_keys,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .order_by("id")
                .all()
            )
        agents_by_key = {agent.agent_key: agent for agent in locked_agents}
        candidate = agents_by_key.get(candidate_key)
        same_assignment = bool(candidate and previous_agent_key == candidate.agent_key)
        candidate_available = bool(
            candidate
            and candidate.status not in {"offline", "error"}
            and (same_assignment or candidate.load < candidate.max_concurrent)
        )

        locked_task.dispatch_status = decision.dispatch_status
        locked_task.dispatch_reason = decision.reason
        previous_agent = agents_by_key.get(previous_agent_key)

        if decision_requests_clarification and not clarification_requested:
            locked_task.dispatch_status = "no_candidate"
            locked_task.dispatch_reason = "lion_agent 澄清决策格式无效，未执行任务"

        if clarification_requested:
            await _release_agent_assignment(previous_agent, locked_task.id, connection)
            locked_task.assigned_agent = None
            locked_task.assigned_agent_key = None
            locked_task.status = COMMAND_TASK_STATUS_PENDING_CLARIFICATION
            locked_task.dispatch_status = "needs_clarification"
            locked_task.dispatch_reason = clarification_question
            locked_task.payload = _payload_with_clarification(locked_task.payload, clarification_question)
            await locked_task.save(using_db=connection)
            await record_task_event(
                locked_task,
                "clarification_requested",
                clarification_question,
                operator_id,
                {"dispatch_source": "lion_agent", "raw_response": getattr(decision, "raw_response", None)},
                using_db=connection,
            )
        elif candidate_available:
            selected_agent = candidate
            locked_task.assigned_agent = candidate
            locked_task.assigned_agent_key = candidate.agent_key
            locked_task.status = "dispatched"

            if previous_agent and previous_agent.agent_key != candidate.agent_key:
                await _release_agent_assignment(previous_agent, locked_task.id, connection)

            if not same_assignment:
                candidate.load += 1
            candidate.current_task_id = locked_task.id
            if candidate.load >= candidate.max_concurrent:
                candidate.status = "busy"
            await candidate.save(
                using_db=connection,
                update_fields=["load", "current_task_id", "status"],
            )
            await locked_task.save(using_db=connection)
            await record_task_event(
                locked_task,
                "task_dispatched",
                decision.reason,
                operator_id,
                {"dispatch_source": "lion_agent", "raw_response": decision.raw_response},
                using_db=connection,
            )
        else:
            await _release_agent_assignment(previous_agent, locked_task.id, connection)
            if decision.agent and not candidate_available:
                locked_task.dispatch_status = "no_candidate"
                locked_task.dispatch_reason = "候选员工容量已变化，请重新调度"
            locked_task.assigned_agent = None
            locked_task.assigned_agent_key = None
            locked_task.status = "pending_dispatch"
            await locked_task.save(using_db=connection)
            await record_task_event(
                locked_task,
                "dispatch_waiting",
                locked_task.dispatch_reason,
                operator_id,
                {"dispatch_source": "lion_agent", "raw_response": decision.raw_response},
                using_db=connection,
            )

    if locked_task is not task:
        for field in ("assigned_agent_key", "dispatch_status", "dispatch_reason", "status", "payload"):
            setattr(task, field, getattr(locked_task, field))
    realtime_reason = "clarification_requested" if clarification_requested else "task_dispatched"
    await publish_command_center_update(locked_task.tenant_id, realtime_reason, locked_task.id)
    if selected_agent and _should_auto_execute(selected_agent):
        _schedule_lion_agent_execution(locked_task, selected_agent, operator_id)
    return locked_task


def _is_digital_employee_agent(agent: CommandAgent) -> bool:
    return agent.runtime == "lion_agent" and bool(agent.role_key)


def _should_auto_execute(agent: CommandAgent) -> bool:
    return _is_digital_employee_agent(agent)


def _schedule_lion_agent_execution(task: CommandTask, employee: CommandAgent, operator_id: str | None = None) -> None:
    async def _runner() -> None:
        current_task = await CommandTask.get_or_none(id=task.id, tenant_id=task.tenant_id, deleted=False)
        current_employee = await CommandAgent.get_or_none(id=employee.id, tenant_id=employee.tenant_id, deleted=False)
        if not current_task or not current_employee:
            return
        from services.command_center.run_manager import RunCheckoutConflict, execute_task_run

        try:
            await execute_task_run(current_task, current_employee, operator_id=operator_id)
        except RunCheckoutConflict:
            return
        except Exception as exc:
            failed_task = await CommandTask.get_or_none(id=task.id, tenant_id=task.tenant_id, deleted=False)
            if failed_task and failed_task.status == "dispatched":
                failed_task.result = str(exc)
                await failed_task.save(update_fields=["result", "utime"])
                await update_task_progress(
                    failed_task,
                    {
                        "status": "failed",
                        "message": f"数字伙伴执行失败: {exc}",
                        "metadata": {"error": str(exc)},
                    },
                    operator_id,
                )

    asyncio.create_task(_runner())


def _schedule_task_dispatch(task: CommandTask, operator_id: str | None = None) -> None:
    async def _runner() -> None:
        current_task = await CommandTask.get_or_none(id=task.id, tenant_id=task.tenant_id, deleted=False)
        if not current_task:
            return
        try:
            await dispatch_task(current_task, operator_id)
        except Exception as exc:
            logger.exception(f"指挥中心任务调度失败: task_id={task.id}")
            failed_task = await CommandTask.get_or_none(id=task.id, tenant_id=task.tenant_id, deleted=False)
            if failed_task and failed_task.status == "pending_dispatch":
                await update_task_progress(
                    failed_task,
                    {
                        "status": "failed",
                        "message": f"指挥部调度失败: {exc}",
                        "metadata": {"error": str(exc)},
                    },
                    operator_id,
                )

    asyncio.create_task(_runner())


async def create_command_task(tenant_id: str, creator, data: dict) -> CommandTask:
    content = task_content_from_data(data)
    compatibility_title = content[:MAX_COMPAT_TASK_TITLE_LENGTH] or DEFAULT_TASK_TITLE
    payload = dict(data.get("payload")) if isinstance(data.get("payload"), dict) else {}
    payload.pop("clarifications", None)
    task = await CommandTask.create(
        tenant_id=tenant_id,
        title=compatibility_title,
        description=content or None,
        source=data.get("source", "manual"),
        priority=data.get("priority", 3),
        required_capabilities=data.get("required_capabilities", []),
        payload=payload,
        requested_agent_id=data.get("agent_id") or data.get("requested_agent_id"),
        room_id=data.get("room_id"),
        creator=creator,
    )
    await record_task_event(task, "task_created", "任务已创建，等待指挥部调度", getattr(creator, "id", None))
    await publish_command_center_update(task.tenant_id, "task_created", task.id)
    _schedule_task_dispatch(task, getattr(creator, "id", None))
    return task


async def answer_task_clarification(
    tenant_id: str,
    task_id: str,
    answer: str,
    operator_id: str | None,
    *,
    owner_only: bool = False,
) -> CommandTask | None:
    if not isinstance(answer, str) or not answer.strip():
        raise InvalidClarificationAnswer("clarification answer is required")
    normalized_answer = answer.strip()
    if len(normalized_answer) > MAX_CLARIFICATION_ANSWER_LENGTH:
        raise InvalidClarificationAnswer("clarification answer is too long")

    async with in_transaction() as connection:
        locked_task = (
            await CommandTask.filter(id=task_id, tenant_id=tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not locked_task:
            return None
        if owner_only and str(getattr(locked_task, "creator_id", None)) != str(operator_id):
            raise TaskClarificationForbidden("only the task creator may answer this clarification")
        if locked_task.status != COMMAND_TASK_STATUS_PENDING_CLARIFICATION:
            raise TaskClarificationConflict("task is not waiting for clarification")

        payload = dict(locked_task.payload) if isinstance(locked_task.payload, dict) else {}
        clarifications = _normalize_clarifications(payload.get("clarifications"))
        pending_index = next(
            (index for index in range(len(clarifications) - 1, -1, -1) if clarifications[index]["answer"] is None),
            None,
        )
        if pending_index is None:
            raise TaskClarificationConflict("task has no unanswered clarification")

        clarifications[pending_index]["answer"] = normalized_answer
        payload["clarifications"] = clarifications[-MAX_CLARIFICATION_HISTORY:]
        locked_task.payload = payload
        locked_task.status = "pending_dispatch"
        locked_task.dispatch_status = "pending"
        locked_task.dispatch_reason = "用户已补充任务信息，等待重新调度"
        locked_task.utime = datetime.now(TZ)
        await locked_task.save(
            using_db=connection,
            update_fields=["payload", "status", "dispatch_status", "dispatch_reason", "utime"],
        )
        await record_task_event(
            locked_task,
            "clarification_answered",
            "用户已补充任务信息，重新进入调度",
            operator_id,
            using_db=connection,
        )

    await publish_command_center_update(locked_task.tenant_id, "clarification_answered", locked_task.id)
    _schedule_task_dispatch(locked_task, operator_id)
    return locked_task


async def apply_task_progress_in_transaction(
    connection,
    task: CommandTask,
    data: dict,
    operator_id: str | None = None,
    *,
    locked_task: CommandTask | None = None,
    expected_run_id: str | None = None,
):
    """Persist task, event, and agent workload changes on the caller's transaction."""
    if locked_task is None:
        locked_task = (
            await CommandTask.filter(id=task.id, tenant_id=task.tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
    if not locked_task or (expected_run_id is not None and locked_task.run_id != expected_run_id):
        return None

    old_status = locked_task.status
    old_assigned_agent_key = locked_task.assigned_agent_key
    if old_status not in TERMINAL_TASK_STATUS:
        if "progress" in data:
            locked_task.progress = max(0, min(100, int(data["progress"])))
        if "status" in data:
            locked_task.status = data["status"]
        if "run_id" in data:
            locked_task.run_id = data["run_id"]
        if "result" in data:
            locked_task.result = data["result"]

    now = datetime.now(TZ)
    if locked_task.status == "running" and not locked_task.started_at:
        locked_task.started_at = now
    if locked_task.status in TERMINAL_TASK_STATUS:
        if not locked_task.finished_at:
            locked_task.finished_at = now
        if locked_task.status == "completed":
            locked_task.progress = 100

    entered_terminal = old_status not in TERMINAL_TASK_STATUS and locked_task.status in TERMINAL_TASK_STATUS

    await locked_task.save(using_db=connection)
    await record_task_event(
        locked_task,
        "task_progress_updated",
        data.get("message"),
        operator_id,
        data.get("metadata"),
        using_db=connection,
    )

    terminal_agent = None
    if entered_terminal and old_assigned_agent_key:
        terminal_agent = (
            await CommandAgent.filter(
                tenant_id=locked_task.tenant_id,
                agent_key=old_assigned_agent_key,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if terminal_agent:
            terminal_agent.load = max(0, terminal_agent.load - 1)
            if terminal_agent.current_task_id == locked_task.id:
                terminal_agent.current_task_id = None
            if terminal_agent.status == "busy" and terminal_agent.load < terminal_agent.max_concurrent:
                terminal_agent.status = "idle"
            await terminal_agent.save(
                using_db=connection,
                update_fields=["load", "current_task_id", "status"],
            )

    return locked_task, terminal_agent, entered_terminal


async def complete_task_progress_after_commit(task: CommandTask, transition, data: dict) -> CommandTask:
    """Run realtime/report side effects only after the owning transaction commits."""
    locked_task, terminal_agent, entered_terminal = transition

    if locked_task is not task:
        for field in ("progress", "status", "run_id", "result", "started_at", "finished_at"):
            setattr(task, field, getattr(locked_task, field))

    if entered_terminal and terminal_agent and _is_digital_employee_agent(terminal_agent):
        try:
            from services.digital_employee.employee_report import compose

            asyncio.create_task(compose(locked_task, (data.get("metadata") or {}).get("actions")))
        except Exception as exc:
            logger.warning(f"调度数字伙伴报告生成失败: task_id={locked_task.id}, error={exc}")

    await publish_command_center_update(locked_task.tenant_id, "task_progress_updated", locked_task.id)
    return locked_task


async def update_task_progress(task: CommandTask, data: dict, operator_id: str | None = None) -> CommandTask:
    async with in_transaction() as connection:
        transition = await apply_task_progress_in_transaction(connection, task, data, operator_id)
    if transition is None:
        return task
    return await complete_task_progress_after_commit(task, transition, data)


async def reconcile_agent_workloads(tenant_id: str | None = None) -> int:
    """按真实活跃任务重建员工负载，修复异常中断留下的状态漂移。"""
    agent_filters = {"deleted": False}
    if tenant_id:
        agent_filters["tenant_id"] = tenant_id
    agents = await CommandAgent.filter(**agent_filters).all()
    if not agents:
        return 0

    reconciled = 0
    reconciled_tenants: set[str | None] = set()
    for agent_ref in agents:
        async with in_transaction():
            agent = await CommandAgent.filter(id=agent_ref.id, deleted=False).select_for_update().first()
            if not agent:
                continue
            assigned_tasks = (
                await CommandTask.filter(
                    tenant_id=agent.tenant_id,
                    assigned_agent_id=agent.id,
                    deleted=False,
                )
                .exclude(status__in=TERMINAL_TASK_STATUS)
                .order_by("-ctime")
                .all()
            )
            expected_load = len(assigned_tasks)
            expected_current_task_id = assigned_tasks[0].id if assigned_tasks else None
            expected_status = agent.status
            if agent.status in {"idle", "busy"}:
                max_concurrent = max(1, int(agent.max_concurrent or 1))
                expected_status = "busy" if expected_load >= max_concurrent else "idle"

            update_fields = []
            if agent.load != expected_load:
                agent.load = expected_load
                update_fields.append("load")
            if agent.current_task_id != expected_current_task_id:
                agent.current_task_id = expected_current_task_id
                update_fields.append("current_task_id")
            if agent.status != expected_status:
                agent.status = expected_status
                update_fields.append("status")
            if update_fields:
                await agent.save(update_fields=update_fields)
                reconciled += 1
                reconciled_tenants.add(agent.tenant_id)

    for reconciled_tenant_id in reconciled_tenants:
        await publish_command_center_update(reconciled_tenant_id, "agent_workloads_reconciled")

    return reconciled


async def list_tasks(tenant_id: str, filters: dict) -> tuple[list[CommandTask], int]:
    condition = Q(tenant_id=tenant_id, deleted=False)
    if filters.get("status"):
        condition &= Q(status=filters["status"])
    if filters.get("agent_key"):
        condition &= Q(assigned_agent_key=filters["agent_key"])
    if filters.get("keyword"):
        keyword = filters["keyword"]
        condition &= Q(title__icontains=keyword) | Q(description__icontains=keyword)

    page = int(filters.get("page", 1))
    size = int(filters.get("size", 20))
    offset = (page - 1) * size
    queryset = CommandTask.filter(condition)
    total = await queryset.count()
    tasks = await queryset.order_by("priority", "-ctime").offset(offset).limit(size).all()
    return tasks, total


async def sweep_stale_dispatch_tasks(operator_id: str | None = None) -> int:
    """把长时间没有调度活动的 pending_dispatch 任务置为失败。"""
    from datetime import timedelta

    from settings import COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS

    deadline = datetime.now(TZ) - timedelta(seconds=COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS)
    stale_tasks = await CommandTask.filter(
        deleted=False,
        status="pending_dispatch",
        utime__lt=deadline,
    ).all()
    swept = 0
    for task in stale_tasks:
        try:
            data = {
                "status": "failed",
                "message": "长时间没有可用的数字伙伴接单，任务已自动超时",
                "metadata": {"reason": "dispatch_timeout", "timeout_seconds": COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS},
            }
            async with in_transaction() as connection:
                locked_task = (
                    await CommandTask.filter(
                        id=task.id,
                        tenant_id=task.tenant_id,
                        deleted=False,
                        status="pending_dispatch",
                        utime__lt=deadline,
                    )
                    .using_db(connection)
                    .select_for_update(skip_locked=True)
                    .first()
                )
                if not locked_task:
                    continue
                transition = await apply_task_progress_in_transaction(
                    connection,
                    locked_task,
                    data,
                    operator_id,
                    locked_task=locked_task,
                )
            if transition is None:
                continue
            await complete_task_progress_after_commit(task, transition, data)
            swept += 1
        except Exception as exc:
            logger.warning(f"派单超时清扫失败: task_id={task.id}, error={exc}")
    return swept
