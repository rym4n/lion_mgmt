# coding=utf-8
from dataclasses import dataclass
from typing import Iterable, Protocol


class AgentCandidate(Protocol):
    id: str
    agent_key: str
    capabilities: list[str]
    status: str
    load: int
    max_concurrent: int


@dataclass(slots=True)
class DispatchDecision:
    agent: AgentCandidate | None
    dispatch_status: str
    reason: str
    requested_agent_id: str | None = None


def normalize_capabilities(capabilities: Iterable[str] | None) -> set[str]:
    return {str(item).strip().lower() for item in (capabilities or []) if str(item).strip()}


def is_agent_available(agent: AgentCandidate) -> bool:
    return agent.status in {"idle", "busy"} and agent.load < agent.max_concurrent


def capability_score(agent: AgentCandidate, required_capabilities: Iterable[str] | None) -> tuple[int, int]:
    required = normalize_capabilities(required_capabilities)
    agent_capabilities = normalize_capabilities(agent.capabilities)
    matched = len(required & agent_capabilities)
    missing = len(required - agent_capabilities)
    return matched, missing


def can_handle(agent: AgentCandidate, required_capabilities: Iterable[str] | None) -> bool:
    return is_agent_available(agent) and capability_score(agent, required_capabilities)[1] == 0


def choose_agent(
    agents: Iterable[AgentCandidate],
    required_capabilities: Iterable[str] | None,
    requested_agent_id: str | None = None,
) -> DispatchDecision:
    """
    根据能力标签选择智能体。

    - 用户没有指定智能体：自动选择最匹配且负载最低的智能体。
    - 用户指定正确：尊重用户指定。
    - 用户指定错误：自动纠正到可处理该任务的智能体。
    - 当前没有可用智能体：返回 no_candidate，任务保持待调度。
    """

    candidates = list(agents)
    requested = None
    if requested_agent_id:
        requested = next((item for item in candidates if item.id == requested_agent_id or item.agent_key == requested_agent_id), None)
        if requested and can_handle(requested, required_capabilities):
            return DispatchDecision(
                agent=requested,
                dispatch_status="user_specified",
                reason="用户指定的智能体可处理该任务，已按指定分配",
                requested_agent_id=requested_agent_id,
            )

    available = [item for item in candidates if can_handle(item, required_capabilities)]
    if not available:
        if requested_agent_id and requested is None:
            reason = "用户指定的智能体不存在，且当前没有可用候选智能体"
        elif requested_agent_id:
            reason = "用户指定的智能体能力或状态不匹配，且当前没有可用候选智能体"
        else:
            reason = "当前没有满足任务能力要求的可用智能体"
        return DispatchDecision(None, "no_candidate", reason, requested_agent_id=requested_agent_id)

    def sort_key(agent: AgentCandidate) -> tuple[int, int, str]:
        matched, _ = capability_score(agent, required_capabilities)
        return (-matched, agent.load, agent.agent_key)

    selected = sorted(available, key=sort_key)[0]
    if requested_agent_id:
        if requested is None:
            reason = f"用户指定的智能体 {requested_agent_id} 不存在，已自动调度到 {selected.agent_key}"
        else:
            reason = f"用户指定的智能体 {requested_agent_id} 能力或状态不匹配，已自动纠正到 {selected.agent_key}"
        return DispatchDecision(selected, "corrected", reason, requested_agent_id=requested_agent_id)

    return DispatchDecision(
        selected,
        "auto_assigned",
        f"未指定智能体，已按能力和负载自动调度到 {selected.agent_key}",
        requested_agent_id=None,
    )
