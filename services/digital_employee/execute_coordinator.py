import orjson
from aiocache import caches
from sanic.log import logger
from typing import Awaitable, Callable

from models.command_center import CommandAgent, CommandTask
from settings import (
    AIOE_INTERNAL_URL,
    WR_TOPIC,
)

from .aioe_credential_manager import get_employee_api_token
from .knowledge_payload import build_knowledge_payload
from .lion_agent_adapter import identity_only_memory, stream_prompt_events
from .prompt_builder import build_runtime_prompt


class RuntimeEventError(RuntimeError):
    """A blocking runtime event, retaining its stable public error payload."""

    def __init__(self, payload: dict):
        self.payload = dict(payload)
        super().__init__(str(self.payload.get("message") or "数字员工运行异常"))


def _stream_id(task: CommandTask) -> str:
    return f"digital-employee:{task.id}"


def _receiver_id(task: CommandTask, operator_id: str | None) -> str | None:
    return operator_id or getattr(task, "creator_id", None)


def _zero_usage() -> dict:
    return {
        "fallback": False,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model_calls": 0,
    }


def _normalize_usage(value) -> dict:
    data = value if isinstance(value, dict) else {}

    def _int(key: str) -> int:
        try:
            return int(data.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    input_tokens = _int("input_tokens")
    output_tokens = _int("output_tokens")
    total_tokens = _int("total_tokens") or input_tokens + output_tokens
    return {
        "fallback": bool(data.get("fallback", False)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "model_calls": _int("model_calls"),
    }


# 契约 D2：lion_mgmt 恒发 both / 5，前端不暴露配置项。
# 这两个值参与 lion_agent 侧 MemoryResource 的 memory_identity，
# 传值确实生效——变化会触发 identity miss 并重建实例。
MEMORY_MODE = "both"
MEMORY_TOP_K = 5


def _build_memory_payload(task: CommandTask, employee: CommandAgent, embedding=None) -> dict:
    """构造 SPEC §5.7 的 payload["memory"] 段。

    记忆的 owner 是**员工**，不是 workspace 目录（SPEC §5.5）：continuation 执行时
    顶层 workspace_id 会切到临时 workspace，但记忆必须仍写员工本体的 .reme，
    否则员工检索不到、且多员工复用同一 continuation workspace 时会串味。
    因此这里只读 employee.id，绝不读调用处的局部变量 workspace_id。
    """
    return {
        "enabled": True,
        "mode": MEMORY_MODE,
        "top_k": MEMORY_TOP_K,
        # 租户身份 ≠ 租户凭证：内嵌模式虽无远程凭证，但 lion_agent 要靠它
        # 校验 workspace 的 owner.json，去掉就把隔离降级为目录命名约定。
        "tenant_id": task.tenant_id,
        "employee_id": employee.id,
        "provenance": {
            "role_key": employee.role_key,
            "task_id": task.id,
            # 与 task_id 当前同源（顶层 request_id 也是 task.id），语义不同：
            # 一个是「哪次请求」、一个是「哪个任务」。契约 §3 要求两键都在。
            "request_id": task.id,
            "source_kind": task.source,
        },
        # Task 7 接入 resolve_llm(purpose="embedding") 后才可能非 None。
        "embedding": embedding.to_payload() if embedding is not None else None,
    }


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
        tool_name = data.get("name") or data.get("tool_name") or data.get("tool") or data.get("tool_call_name") or "tool"
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
    from libs.error_code.exception import CustomError
    from services.command_center.manager import update_task_progress
    from services.digital_employee.manager import sync_employee_workspace
    from services.llm_config.resolver import resolve_llm

    employee = await sync_employee_workspace(employee, force=True)
    workspace_id = employee.id
    if continuation_workspace:
        from services.digital_employee.lion_agent_client import LionAgentClient

        await LionAgentClient().sync_workspace(
            continuation_workspace["workspace_id"],
            continuation_workspace["files"],
            role_version=continuation_workspace.get("role_version"),
            skill_versions=continuation_workspace.get("skill_versions") or {},
            tenant_id=task.tenant_id,
            # continuation workspace 是该员工的派生 workspace，归属仍是员工本体
            employee_id=employee.id,
        )
        workspace_id = continuation_workspace["workspace_id"]
    embedding = None
    if not (command_run is not None and continuation_workspace is None):
        try:
            embedding = await resolve_llm(task.tenant_id, purpose="embedding")
        except CustomError:
            embedding = None
    # Command-center dispatches are independent task runs.  They still use the
    # employee workspace and its role/skill snapshot, but must not recall or
    # write employee-level long-term memory: a previous task's volatile report
    # or download URL is not evidence for the current task.
    if command_run is not None and continuation_workspace is None:
        memory_section = identity_only_memory(task.tenant_id)
    else:
        memory_section = _build_memory_payload(task, employee, embedding=embedding)
    aioe_token = await get_employee_api_token(employee)
    knowledge_section = await build_knowledge_payload(employee)
    llm = await resolve_llm(
        task.tenant_id, config_id=getattr(employee, "llm_config_id", None)
    )
    payload = {
        "workspace_id": workspace_id,
        "session_id": getattr(command_run, "run_id", None) or task.run_id or task.id,
        "request_id": task.id,
        "message": build_runtime_prompt(task, employee),
        "memory": memory_section,
        "aioe": {
            "base_url": AIOE_INTERNAL_URL,
            "token": aioe_token,
            "tenant_id": task.tenant_id,
            # 本次数字员工运行的 run_id，随 aioe_cli 回传到 /playbook/debug，
            # 用于把动作/剧本产生的 runlog 关联到该 CommandTaskRun（见 execute_action）。
            "agent_run_id": getattr(command_run, "run_id", None) or task.run_id,
        },
        "llm": llm,
    }
    if knowledge_section:
        payload["knowledge"] = knowledge_section
    content = []
    saw_delta = False
    usage = _zero_usage()
    await update_task_progress(task, {"status": "running", "progress": max(task.progress or 0, 1), "message": "数字员工开始执行"}, operator_id)
    async for event_type, event in stream_prompt_events(**payload):
        runtime_event: dict | None = None
        if on_runtime_event:
            runtime_event = await on_runtime_event(event_type, event)
            if runtime_event and runtime_event.get("event_type") in {"approval_required", "policy_denied"}:
                return {
                    "content": "".join(content),
                    "usage": usage,
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
                usage = _normalize_usage(event.get("usage"))
                if value and not saw_delta:
                    content.append(str(value))
                    await _publish_room_event(task, "delta", {"content": value}, operator_id)
                await _publish_room_end(task, operator_id)
        elif event_type == "error":
            # The callback has already persisted the structured runtime error.
            # Non-blocking errors are diagnostics and must not abort the task;
            # only an explicitly blocking event ends the business execution.
            if event.get("blocking", True) is False:
                await _publish_room_event(task, "runtime_degraded", event, operator_id)
                continue
            raise RuntimeEventError(event)
        elif event_type == "runtime_degraded":
            # Compression/memory degradation is recorded by the callback and
            # remains visible to the operator, but does not change task status.
            await _publish_room_event(task, event_type, event, operator_id)
        else:
            await _publish_room_event(task, event_type, event, operator_id)
    result_content = "".join(content)
    if command_run is None:
        task.result = result_content
        await task.save(update_fields=["result", "utime"])
        await update_task_progress(
            task,
            {"status": "completed", "progress": 100, "message": "数字员工执行完成"},
            operator_id,
        )
    return {"content": result_content, "usage": usage}
