import orjson
from aiocache import caches
from sanic.log import logger
from typing import Awaitable, Callable

from models.command_center import CommandAgent, CommandTask
from settings import LION_MEMORY_ENDPOINT, WR_TOPIC

from .cognee_credential_manager import get_or_refresh_jwt
from .lion_agent_adapter import stream_prompt_events
from .prompt_builder import build_runtime_prompt


def _stream_id(task: CommandTask) -> str:
    return f"digital-employee:{task.id}"


def _receiver_id(task: CommandTask, operator_id: str | None) -> str | None:
    return operator_id or getattr(task, "creator_id", None)


async def _publish_room_event(task: CommandTask, event_type: str, payload: dict, operator_id: str | None) -> bool:
    if not task.room_id:
        return False
    receiver_id = _receiver_id(task, operator_id)
    if not receiver_id:
        return False

    message: dict | None = None
    if event_type == "delta":
        content = str(payload.get("content") or payload.get("delta") or "")
        if content:
            message = {
                "type": "ai-reply-delta",
                "content": content,
                "room_id": task.room_id,
                "receiver_id": receiver_id,
                "tenant_id": task.tenant_id,
                "stream_id": _stream_id(task),
            }
    elif event_type == "thinking":
        content = str(payload.get("content") or "")
        if content:
            message = {
                "type": "ai-thinking",
                "content": content,
                "room_id": task.room_id,
                "receiver_id": receiver_id,
                "tenant_id": task.tenant_id,
                "stream_id": _stream_id(task),
            }
    elif event_type == "tool_call":
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        tool_name = data.get("name") or data.get("tool_name") or data.get("tool") or "tool"
        status = payload.get("status") or data.get("status") or "started"
        message = {
            "type": "ai-thinking",
            "content": f"正在执行 {tool_name}" if status == "started" else f"{tool_name} 执行完成",
            "tool_name": tool_name,
            "room_id": task.room_id,
            "receiver_id": receiver_id,
            "tenant_id": task.tenant_id,
            "stream_id": _stream_id(task),
        }

    if not message:
        return False
    try:
        client = caches.get("redis_chat").client
        await client.publish(WR_TOPIC, orjson.dumps(message).decode())
        return True
    except Exception as exc:
        logger.warning(f"发布数字员工作战室流式消息失败: task_id={task.id}, error={exc}")
        return False


async def _publish_room_end(task: CommandTask, operator_id: str | None) -> None:
    if not task.room_id:
        return
    receiver_id = _receiver_id(task, operator_id)
    if not receiver_id:
        return
    try:
        client = caches.get("redis_chat").client
        await client.publish(
            WR_TOPIC,
            orjson.dumps(
                {
                    "type": "ai-reply-end",
                    "content": "",
                    "room_id": task.room_id,
                    "receiver_id": receiver_id,
                    "tenant_id": task.tenant_id,
                    "stream_id": _stream_id(task),
                }
            ).decode(),
        )
    except Exception as exc:
        logger.warning(f"发布数字员工作战室结束消息失败: task_id={task.id}, error={exc}")


RuntimeEventHandler = Callable[[str, dict], Awaitable[dict | None]]


async def run(
    task: CommandTask,
    employee: CommandAgent,
    operator_id: str | None = None,
    command_run=None,
    on_runtime_event: RuntimeEventHandler | None = None,
    continuation_workspace: dict | None = None,
) -> dict:
    from services.command_center.manager import update_task_progress
    from services.digital_employee.manager import sync_employee_workspace

    employee = await sync_employee_workspace(employee, force=True)
    workspace_id = employee.id
    if continuation_workspace:
        from services.digital_employee.lion_agent_client import LionAgentClient

        await LionAgentClient().sync_workspace(
            continuation_workspace["workspace_id"],
            continuation_workspace["files"],
            role_version=continuation_workspace.get("role_version"),
            skill_versions=continuation_workspace.get("skill_versions") or {},
        )
        workspace_id = continuation_workspace["workspace_id"]
    jwt = await get_or_refresh_jwt(task.tenant_id)
    payload = {
        "workspace_id": workspace_id,
        "session_id": getattr(command_run, "run_id", None) or task.run_id or task.id,
        "request_id": task.id,
        "message": build_runtime_prompt(task, employee),
        "cognee": {
            "endpoint": LION_MEMORY_ENDPOINT,
            "jwt": jwt,
            "tenant_id": task.tenant_id,
            "dataset_name": employee.id,
            "role_key": employee.role_key,
            "task_id": task.id,
            "source_kind": task.source,
        },
    }
    content = []
    saw_delta = False
    await update_task_progress(task, {"status": "running", "progress": max(task.progress or 0, 1), "message": "数字员工开始执行"}, operator_id)
    async for event_type, event in stream_prompt_events(**payload):
        runtime_event: dict | None = None
        if on_runtime_event:
            runtime_event = await on_runtime_event(event_type, event)
            if runtime_event and runtime_event.get("event_type") in {"approval_required", "policy_denied"}:
                return {
                    "content": "".join(content),
                    "policy": runtime_event["event_type"],
                    "run_id": getattr(command_run, "run_id", None) or task.run_id,
                    "runtime_event": runtime_event,
                }
        if event_type in {"delta", "end"}:
            value = event.get("content") or event.get("delta")
            if event_type == "delta" and value:
                saw_delta = True
                content.append(str(value))
                await _publish_room_event(task, event_type, event, operator_id)
            elif event_type == "end":
                if value and not saw_delta:
                    content.append(str(value))
                    await _publish_room_event(task, "delta", {"content": value}, operator_id)
                await _publish_room_end(task, operator_id)
        elif event_type == "error":
            raise RuntimeError(event.get("message") or event)
        else:
            await _publish_room_event(task, event_type, event, operator_id)
    task.result = "".join(content)
    await task.save(update_fields=["result", "utime"])
    await update_task_progress(
        task,
        {"status": "completed", "progress": 100, "message": "数字员工执行完成"},
        operator_id,
    )
    return {"content": task.result}
