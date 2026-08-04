# coding=utf-8
from collections.abc import AsyncIterator
from typing import Any

import orjson

from .lion_agent_client import LionAgentClient


def workspace_id_for(tenant_id: str, *parts: str) -> str:
    safe_parts = [str(part).replace("/", "_").replace("\\", "_") for part in parts if part]
    return ":".join([str(tenant_id), *safe_parts])


async def execute_prompt(
    *,
    workspace_id: str,
    session_id: str,
    message: str,
    request_id: str | None = None,
    cognee: dict[str, Any] | None = None,
    aioe: dict[str, Any] | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    timeout: float | None = None,
) -> str:
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "session_id": session_id,
        "request_id": request_id,
        "message": message,
        "cognee": cognee,
        "aioe": aioe,
    }
    if model:
        payload["model"] = model
    if system_prompt is not None:
        payload["system_prompt"] = system_prompt
    client = LionAgentClient(timeout=timeout) if timeout else LionAgentClient()
    response = await client.execute(payload)
    if isinstance(response, dict):
        if "content" in response:
            return str(response.get("content") or "")
        data = response.get("data")
        if isinstance(data, dict) and "content" in data:
            return str(data.get("content") or "")
    return str(response or "")


async def stream_prompt_events(
    *,
    workspace_id: str,
    session_id: str,
    message: str,
    request_id: str | None = None,
    cognee: dict[str, Any] | None = None,
    aioe: dict[str, Any] | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "session_id": session_id,
        "request_id": request_id,
        "message": message,
        "cognee": cognee,
        "aioe": aioe,
    }
    if model:
        payload["model"] = model
    if system_prompt is not None:
        payload["system_prompt"] = system_prompt
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
