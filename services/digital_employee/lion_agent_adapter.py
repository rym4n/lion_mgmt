# coding=utf-8
import uuid
from collections.abc import AsyncIterator
from typing import Any

import orjson

from .lion_agent_client import LionAgentClient


def workspace_id_for(tenant_id: str, *parts: str) -> str:
    safe_parts = [str(part).replace("/", "_").replace("\\", "_") for part in parts if part]
    return ":".join([str(tenant_id), *safe_parts])


def identity_only_memory(
    tenant_id: str,
    employee_id: str | None = None,
) -> dict[str, Any]:
    """构造不启用长期记忆但保留 owner 身份的 memory 段（D36 / D40）。

    巡检、报告批注、Agent 后台任务、指挥中心调度协调器这些非员工链路都不需要
    长期记忆，但**仍必须表明自己是谁**——lion_agent 的 internal token 是当前唯一的横向
    边界，不带身份等于「持 token 者可在任意 workspace 执行」。

    enabled=False 只决定「不挂载 middleware」，**不决定「不校验身份」**：
    D40 已裁决归属校验不受 enabled 门控、且在 enabled 判断之前执行。
    不要因为「反正没启用记忆」就把 tenant_id 省掉——那会让 P4 在 1B
    翻转 LION_AGENT_REQUIRE_WORKSPACE_IDENTITY=true 的门槛永远达不到。

    非员工 workspace 默认 employee_id=None；独立数字员工任务仍传入员工身份，
    只关闭记忆 middleware，不绕过 workspace owner 校验。
    """
    return {
        "enabled": False,
        "tenant_id": tenant_id,
        "employee_id": employee_id,
    }



async def execute_prompt(
    *,
    workspace_id: str,
    session_id: str,
    message: str,
    request_id: str | None = None,
    # SPEC §5.7 新契约段；与下方 cognee 由调用方按 RY_MEMORY_BACKEND 互斥赋值。
    # 阶段一两个参数位并存：删 cognee 属 SPEC §8 第 9 步的不可逆清理。
    memory: dict[str, Any] | None = None,
    cognee: dict[str, Any] | None = None,
    aioe: dict[str, Any] | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    llm: Any | None = None,
    response_format: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> str:
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "session_id": session_id,
        "request_id": request_id,
        "message": message,
        "memory": memory,
        "cognee": cognee,
        "aioe": aioe,
        "execution_id": uuid.uuid4().hex,
    }
    if llm is not None:
        payload["llm"] = llm.to_payload()
        # 必须同时写顶层 model：lion_agent 的凭证与模型名走两条独立通道，
        # api_key/base_url 取自 llm 字典，模型名却取自顶层 model 字段
        # （agentscope_runtime.resolve_model_name），它不读 llm["model"]。
        # 只传 llm 会让模型名退回 lion_agent 的 LION_AGENT_MODEL 默认值 aioe，
        # 出现「凭证换了、模型名没换」的静默错配——指挥中心的 lite 档位会失效。
        payload["model"] = llm.model_name
    if model:
        payload["model"] = model
    if system_prompt is not None:
        payload["system_prompt"] = system_prompt
    if response_format is not None:
        payload["response_format"] = response_format
    client = LionAgentClient(timeout=timeout) if timeout else LionAgentClient()
    response = await client.execute(payload)
    if isinstance(response, dict):
        if "content" in response:
            return str(response.get("content") or "")
        data = response.get("data")
        if isinstance(data, dict) and "content" in data:
            return str(data.get("content") or "")
    return str(response or "")


async def chat_with_agent(
    message: str,
    *,
    employee_id: str | None = None,
    tenant_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Send a short notification to a digital employee workspace.

    Ticket notifications do not need a new task/run record.  When a configured
    employee is available its workspace is used directly; otherwise the tenant
    workspace remains a useful fallback for legacy flow definitions.
    """
    workspace_id = str(employee_id or workspace_id_for(tenant_id or "system", "ticket"))
    conversation_id = session_id or f"ticket:{workspace_id}"
    return await execute_prompt(
        workspace_id=workspace_id,
        session_id=conversation_id,
        message=message,
        request_id=f"{conversation_id}:{uuid.uuid4().hex}",
    )


async def stream_prompt_events(
    *,
    workspace_id: str,
    session_id: str,
    message: str,
    request_id: str | None = None,
    memory: dict[str, Any] | None = None,
    cognee: dict[str, Any] | None = None,
    aioe: dict[str, Any] | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    llm: Any | None = None,
    response_format: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "session_id": session_id,
        "request_id": request_id,
        "message": message,
        "memory": memory,
        "cognee": cognee,
        "aioe": aioe,
        "execution_id": uuid.uuid4().hex,
    }
    if llm is not None:
        payload["llm"] = llm.to_payload()
        # 必须同时写顶层 model：lion_agent 的凭证与模型名走两条独立通道，
        # api_key/base_url 取自 llm 字典，模型名却取自顶层 model 字段
        # （agentscope_runtime.resolve_model_name），它不读 llm["model"]。
        # 只传 llm 会让模型名退回 lion_agent 的 LION_AGENT_MODEL 默认值 aioe，
        # 出现「凭证换了、模型名没换」的静默错配——指挥中心的 lite 档位会失效。
        payload["model"] = llm.model_name
    if model:
        payload["model"] = model
    if system_prompt is not None:
        payload["system_prompt"] = system_prompt
    if response_format is not None:
        payload["response_format"] = response_format
    event_name = "message"
    async for line in LionAgentClient().stream_execute(payload):
        if line.startswith("event:"):
            event_name = line[6:].strip() or "message"
            continue
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            data = orjson.loads(raw)
        except orjson.JSONDecodeError:
            data = {"content": raw}
        if not isinstance(data, dict):
            data = {"content": data}
        yield event_name, data
