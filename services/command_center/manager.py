# coding=utf-8
import asyncio
import re
from datetime import datetime

from sanic.log import logger
from tortoise.expressions import Q

from models.command_center import CommandAgent, CommandTask, CommandTaskEvent
from settings import TZ

from .dispatcher import choose_agent_with_lion_agent
from .realtime import publish_command_center_update


TERMINAL_TASK_STATUS = {"completed", "failed", "cancelled", "handed_off"}
DEFAULT_COORDINATOR_AGENT_KEY = "command_center_coordinator"
DEFAULT_COORDINATOR_AGENT_NAME = "指挥部分工协调智能体"
DEFAULT_COORDINATOR_CAPABILITIES = ["command_center", "coordination", "dispatch", "task_planning"]
DEFAULT_TASK_TITLE = "未命名任务"
MAX_TASK_TITLE_LENGTH = 32


def derive_task_title(data: dict) -> str:
    explicit_title = str(data.get("title") or "").strip()
    if explicit_title:
        return _truncate_title(explicit_title)

    content = _task_content_for_title(data)
    if not content:
        return DEFAULT_TASK_TITLE

    sentences = [
        item.strip(" ，,。.!！?？:：;；")
        for item in re.split(r"[。.!！?？;；\n\r]+", content)
        if item.strip(" ，,。.!！?？:：;；")
    ]
    candidate = next(
        (item for item in sentences if not _is_generic_task_intro(item)),
        sentences[0] if sentences else content,
    )
    candidate = _strip_task_intro(candidate)
    return _truncate_title(candidate or content)


def has_task_content(data: dict) -> bool:
    return bool(str(data.get("title") or "").strip() or _task_content_for_title(data))


def _task_content_for_title(data: dict) -> str:
    parts: list[str] = []
    for key in ("description", "content", "prompt", "message", "query", "question"):
        value = data.get(key)
        if value:
            parts.append(str(value))

    payload = data.get("payload")
    if isinstance(payload, dict):
        for key in ("description", "content", "prompt", "message", "query", "question", "task"):
            value = payload.get(key)
            if value:
                parts.append(str(value))

    normalized = "\n".join(re.sub(r"[ \t]+", " ", part).strip() for part in parts)
    return re.sub(r"\n{2,}", "\n", normalized).strip()


def _is_generic_task_intro(value: str) -> bool:
    return value.strip(" ，,。.!！?？:：;；") in {
        "请处理",
        "帮我处理",
        "帮忙处理",
        "请帮我处理",
        "任务如下",
        "需求如下",
        "问题如下",
    }


def _strip_task_intro(value: str) -> str:
    text = value.strip()
    for prefix in ("请你", "请帮我", "帮我", "麻烦", "帮忙", "请", "需要", "协助"):
        if text.startswith(prefix) and len(text) > len(prefix) + 2:
            text = text[len(prefix) :].lstrip(" ，,。.!！?？:：;；")
            break
    return text


def _truncate_title(value: str) -> str:
    title = re.sub(r"\s+", " ", value).strip(" ，,。.!！?？:：;；")
    if not title:
        return DEFAULT_TASK_TITLE
    if len(title) <= MAX_TASK_TITLE_LENGTH:
        return title
    return f"{title[:MAX_TASK_TITLE_LENGTH].rstrip()}..."


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
) -> CommandTaskEvent:
    return await CommandTaskEvent.create(
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


async def dispatch_task(task: CommandTask, operator_id: str | None = None) -> CommandTask:
    previous_agent_key = task.assigned_agent_key
    coordinator = await ensure_default_coordinator_agent(task.tenant_id)
    agents = await CommandAgent.filter(tenant_id=task.tenant_id, deleted=False).all()
    decision = await choose_agent_with_lion_agent(task, agents, coordinator)

    task.dispatch_status = decision.dispatch_status
    task.dispatch_reason = decision.reason

    if decision.agent:
        agent = decision.agent
        task.assigned_agent = agent
        task.assigned_agent_key = agent.agent_key
        task.status = "dispatched"
        await task.save()

        if previous_agent_key and previous_agent_key != agent.agent_key:
            previous_agent = await CommandAgent.get_or_none(tenant_id=task.tenant_id, agent_key=previous_agent_key, deleted=False)
            if previous_agent:
                previous_agent.load = max(0, previous_agent.load - 1)
                if previous_agent.current_task_id == task.id:
                    previous_agent.current_task_id = None
                if previous_agent.status == "busy" and previous_agent.load < previous_agent.max_concurrent:
                    previous_agent.status = "idle"
                await previous_agent.save(update_fields=["load", "current_task_id", "status"])

        if previous_agent_key != agent.agent_key:
            agent.load = min(agent.max_concurrent, agent.load + 1)
        agent.current_task_id = task.id
        if agent.load >= agent.max_concurrent:
            agent.status = "busy"
        await agent.save(update_fields=["load", "current_task_id", "status"])
        await record_task_event(task, "task_dispatched", decision.reason, operator_id, {"dispatch_source": "lion_agent", "raw_response": decision.raw_response})
        if _should_auto_execute(agent):
            _schedule_lion_agent_execution(task, agent, operator_id)
    else:
        if previous_agent_key:
            previous_agent = await CommandAgent.get_or_none(tenant_id=task.tenant_id, agent_key=previous_agent_key, deleted=False)
            if previous_agent:
                previous_agent.load = max(0, previous_agent.load - 1)
                if previous_agent.current_task_id == task.id:
                    previous_agent.current_task_id = None
                if previous_agent.status == "busy" and previous_agent.load < previous_agent.max_concurrent:
                    previous_agent.status = "idle"
                await previous_agent.save(update_fields=["load", "current_task_id", "status"])
        task.assigned_agent = None
        task.assigned_agent_key = None
        task.status = "pending_dispatch"
        await task.save()
        await record_task_event(task, "dispatch_waiting", decision.reason, operator_id, {"dispatch_source": "lion_agent", "raw_response": decision.raw_response})

    await publish_command_center_update(task.tenant_id, "task_dispatched", task.id)
    return task


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
        try:
            from services.command_center.run_manager import execute_task_run

            await execute_task_run(current_task, current_employee, operator_id=operator_id)
        except Exception as exc:
            failed_task = await CommandTask.get_or_none(id=task.id, tenant_id=task.tenant_id, deleted=False)
            if failed_task and failed_task.status not in TERMINAL_TASK_STATUS:
                failed_task.result = str(exc)
                await failed_task.save(update_fields=["result", "utime"])
                await update_task_progress(
                    failed_task,
                    {
                        "status": "failed",
                        "message": f"数字员工执行失败: {exc}",
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
            if failed_task and failed_task.status not in TERMINAL_TASK_STATUS:
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
    title = derive_task_title(data)
    task = await CommandTask.create(
        tenant_id=tenant_id,
        title=title,
        description=data.get("description"),
        source=data.get("source", "manual"),
        priority=data.get("priority", 3),
        required_capabilities=data.get("required_capabilities", []),
        payload=data.get("payload", {}),
        requested_agent_id=data.get("agent_id") or data.get("requested_agent_id"),
        room_id=data.get("room_id"),
        creator=creator,
    )
    await record_task_event(task, "task_created", "任务已创建，等待指挥部调度", getattr(creator, "id", None))
    await publish_command_center_update(task.tenant_id, "task_created", task.id)
    _schedule_task_dispatch(task, getattr(creator, "id", None))
    return task


async def update_task_progress(task: CommandTask, data: dict, operator_id: str | None = None) -> CommandTask:
    old_assigned_agent_key = task.assigned_agent_key

    if "progress" in data:
        task.progress = max(0, min(100, int(data["progress"])))
    if "status" in data:
        task.status = data["status"]
    if "run_id" in data:
        task.run_id = data["run_id"]

    now = datetime.now(TZ)
    if task.status == "running" and not task.started_at:
        task.started_at = now
    if task.status in TERMINAL_TASK_STATUS:
        task.finished_at = now
        if task.status == "completed":
            task.progress = 100

    await task.save()
    await record_task_event(task, "task_progress_updated", data.get("message"), operator_id, data.get("metadata"))

    if task.status in TERMINAL_TASK_STATUS and old_assigned_agent_key:
        agent = await CommandAgent.get_or_none(tenant_id=task.tenant_id, agent_key=old_assigned_agent_key, deleted=False)
        if agent:
            agent.load = max(0, agent.load - 1)
            if agent.current_task_id == task.id:
                agent.current_task_id = None
            if agent.status == "busy" and agent.load < agent.max_concurrent:
                agent.status = "idle"
            await agent.save(update_fields=["load", "current_task_id", "status"])

    if task.status in TERMINAL_TASK_STATUS and task.assigned_agent_key:
        try:
            agent = await CommandAgent.get_or_none(tenant_id=task.tenant_id, agent_key=task.assigned_agent_key, deleted=False)
            if agent and _is_digital_employee_agent(agent):
                from services.digital_employee.employee_report import compose

                asyncio.create_task(compose(task, (data.get("metadata") or {}).get("actions")))
        except Exception as exc:
            logger.warning(f"调度数字员工报告生成失败: task_id={task.id}, error={exc}")

    await publish_command_center_update(task.tenant_id, "task_progress_updated", task.id)
    return task


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
    """把长时间卡在 pending_dispatch 的任务置为失败，避免无可用数字员工时静默滞留。"""
    from datetime import timedelta

    from settings import COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS

    deadline = datetime.now(TZ) - timedelta(seconds=COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS)
    stale_tasks = await CommandTask.filter(
        deleted=False,
        status="pending_dispatch",
        ctime__lt=deadline,
    ).all()
    swept = 0
    for task in stale_tasks:
        try:
            await update_task_progress(
                task,
                {
                    "status": "failed",
                    "message": "长时间没有可用的数字员工接单，任务已自动超时",
                    "metadata": {"reason": "dispatch_timeout", "timeout_seconds": COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS},
                },
                operator_id,
            )
            swept += 1
        except Exception as exc:
            logger.warning(f"派单超时清扫失败: task_id={task.id}, error={exc}")
    return swept
