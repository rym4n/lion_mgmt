# coding=utf-8
from dataclasses import dataclass
from typing import Iterable, Protocol

import orjson
from sanic.log import logger

from services.digital_employee.lion_agent_adapter import execute_prompt, workspace_id_for


class AgentCandidate(Protocol):
    id: str
    agent_key: str
    name: str
    description: str | None
    capabilities: list[str]
    status: str
    load: int
    max_concurrent: int
    metadata: dict
    runtime: str
    is_builtin: bool
    role_key: str | None


class DispatchTask(Protocol):
    id: str
    tenant_id: str
    title: str
    description: str | None
    source: str
    priority: int
    status: str
    progress: int
    required_capabilities: list[str]
    payload: dict
    requested_agent_id: str | None
    room_id: str | None
    creator_id: str | None


@dataclass(slots=True)
class DispatchDecision:
    agent: AgentCandidate | None
    dispatch_status: str
    reason: str
    requested_agent_id: str | None = None
    raw_response: str | None = None


def is_agent_available(agent: AgentCandidate) -> bool:
    return agent.status in {"idle", "busy"} and agent.load < agent.max_concurrent


def _agent_payload(agent: AgentCandidate) -> dict:
    return {
        "id": agent.id,
        "agent_key": agent.agent_key,
        "name": agent.name,
        "description": agent.description,
        "capabilities": agent.capabilities or [],
        "status": agent.status,
        "load": agent.load,
        "max_concurrent": agent.max_concurrent,
        "available": is_agent_available(agent),
        "metadata": agent.metadata or {},
        "runtime": getattr(agent, "runtime", "lion_agent"),
        "is_builtin": getattr(agent, "is_builtin", False),
        "role_key": getattr(agent, "role_key", None),
    }


def _task_payload(task: DispatchTask) -> dict:
    return {
        "id": task.id,
        "tenant_id": task.tenant_id,
        "content": task.description or task.title,
        "source": task.source,
        "priority": task.priority,
        "status": task.status,
        "progress": task.progress,
        "required_capabilities": task.required_capabilities or [],
        "payload": task.payload or {},
        "requested_agent_id": task.requested_agent_id,
        "room_id": task.room_id,
        "creator_id": task.creator_id,
    }


def _extract_json_object(text: str) -> dict:
    if not text or not text.strip():
        raise ValueError("lion_agent returned empty dispatch decision")
    content = text.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("lion_agent dispatch decision is not JSON")
    return orjson.loads(content[start : end + 1])


def _find_agent(agents: Iterable[AgentCandidate], agent_id_or_key: str | None) -> AgentCandidate | None:
    if not agent_id_or_key:
        return None
    return next((item for item in agents if item.id == agent_id_or_key or item.agent_key == agent_id_or_key), None)


def _is_digital_employee_candidate(agent: AgentCandidate) -> bool:
    metadata = getattr(agent, "metadata", {}) or {}
    return bool(getattr(agent, "role_key", None)) or metadata.get("digital_employee") is True


def _requires_digital_employee(task: DispatchTask) -> bool:
    payload = task.payload or {}
    return payload.get("_dispatch_scope") == "digital_employee"


def _normalize_dispatch_status(value: str | None, requested_agent_id: str | None) -> str:
    allowed = {"user_specified", "corrected", "auto_assigned", "no_candidate"}
    if value in allowed:
        return value
    return "corrected" if requested_agent_id else "auto_assigned"


def _build_dispatch_prompt(task: DispatchTask, agents: list[AgentCandidate], coordinator: AgentCandidate | None) -> str:
    message_context = {
        "task": _task_payload(task),
        "candidate_agents": [_agent_payload(agent) for agent in agents],
        "rules": [
            "只能从 candidate_agents 中选择一个 available=true 的智能体。",
            "如果 requested_agent_id 存在且该智能体 available=true，可以优先尊重用户指定。",
            "如果用户指定的智能体不存在或不可用，可以选择其他 available=true 的智能体。",
            "如果没有任何 available=true 的智能体，返回 agent_key=null 且 dispatch_status=no_candidate。",
            "只返回 JSON，不要输出解释性正文。",
        ],
        "response_schema": {
            "agent_key": "string|null，选择的智能体agent_key；没有候选时为null",
            "dispatch_status": "user_specified|corrected|auto_assigned|no_candidate",
            "reason": "中文说明，说明为什么这样分配",
        },
    }
    return f"""<system_instruction>
你是指挥中心调度智能体，需要为任务选择最合适的可用智能体。
</system_instruction>

<message_context>
{orjson.dumps(message_context, option=orjson.OPT_INDENT_2).decode()}
</message_context>

<user_message>
请根据任务内容和候选智能体状态，为该任务分配一个最合适的智能体，并严格按 response_schema 返回 JSON。
</user_message>"""


async def choose_agent_with_lion_agent(
    task: DispatchTask,
    agents: Iterable[AgentCandidate],
    coordinator: AgentCandidate | None = None,
) -> DispatchDecision:
    """使用 lion_agent 判断指挥中心任务应该分配给哪个智能体。"""

    candidates = [agent for agent in agents if not coordinator or agent.id != coordinator.id]
    if _requires_digital_employee(task):
        candidates = [agent for agent in candidates if _is_digital_employee_candidate(agent)]
    requested_agent_id = task.requested_agent_id
    if not candidates:
        return DispatchDecision(None, "no_candidate", "当前没有可分配的候选智能体", requested_agent_id=requested_agent_id)
    requested_agent = _find_agent(candidates, requested_agent_id)
    if requested_agent and is_agent_available(requested_agent):
        return DispatchDecision(
            requested_agent,
            "user_specified",
            f"按用户指定分配给 {requested_agent.name}",
            requested_agent_id=requested_agent_id,
        )

    session_key = f"command-center-dispatch:{task.id}"
    prompt = _build_dispatch_prompt(task, candidates, coordinator)

    try:
        raw_response = await execute_prompt(
            workspace_id=workspace_id_for(task.tenant_id, "command-center"),
            session_id=session_key,
            request_id=str(task.id),
            message=prompt,
        )
        decision_payload = _extract_json_object(raw_response)
    except Exception as exc:
        logger.error(f"lion_agent 调度决策失败: task_id={task.id}, error={exc}")
        return DispatchDecision(None, "no_candidate", f"lion_agent 调度决策失败: {exc}", requested_agent_id=requested_agent_id)

    selected_key = decision_payload.get("agent_key") or decision_payload.get("agent_id")
    selected_agent = _find_agent(candidates, selected_key)
    reason = decision_payload.get("reason") or "lion_agent 已完成调度判断"

    if not selected_key:
        return DispatchDecision(None, "no_candidate", reason, requested_agent_id=requested_agent_id, raw_response=raw_response)
    if not selected_agent:
        return DispatchDecision(
            None,
            "no_candidate",
            f"lion_agent 返回的智能体 {selected_key} 不存在于候选列表，无法分配",
            requested_agent_id=requested_agent_id,
            raw_response=raw_response,
        )
    if not is_agent_available(selected_agent):
        return DispatchDecision(
            None,
            "no_candidate",
            f"lion_agent 返回的智能体 {selected_agent.agent_key} 当前不可用，无法分配",
            requested_agent_id=requested_agent_id,
            raw_response=raw_response,
        )

    dispatch_status = _normalize_dispatch_status(decision_payload.get("dispatch_status"), requested_agent_id)
    return DispatchDecision(selected_agent, dispatch_status, reason, requested_agent_id=requested_agent_id, raw_response=raw_response)
