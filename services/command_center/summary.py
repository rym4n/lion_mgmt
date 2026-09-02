# coding=utf-8
from __future__ import annotations

from datetime import datetime
from typing import Any


TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "handed_off"}
EVENT_PHRASES = {
    "task_created": "任务已创建",
    "clarification_requested": "等待用户补充任务信息",
    "clarification_answered": "用户已补充任务信息",
    "task_dispatched": "任务已分派",
    "dispatch_waiting": "等待可用数字伙伴",
    "task_progress_updated": "任务进度已更新",
    "run_checked_out": "数字伙伴已接手",
    "runtime_delta": "数字伙伴正在回复",
    "runtime_thinking": "数字伙伴正在分析",
    "runtime_tool_call": "数字伙伴正在执行工具",
    "runtime_progress": "数字伙伴正在推进",
    "runtime_completed": "数字伙伴运行完成",
    "runtime_error": "数字伙伴运行异常",
    "runtime_degraded": "数字伙伴运行已降级继续",
    "runtime_memory": "记忆写回状态已记录",
    "approval_required": "等待人工审批",
    "policy_denied": "策略已阻止风险操作",
    "continuation_started": "审批通过后继续执行",
    "run_cancelled": "运行已取消",
    "run_handed_off": "已人工接管",
    "run_stale_recovered": "运行租约已过期",
}


def _safe_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def zero_usage() -> dict[str, Any]:
    return {
        "fallback": False,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model_calls": 0,
    }


def normalize_usage(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    input_tokens = _safe_int(data.get("input_tokens"))
    output_tokens = _safe_int(data.get("output_tokens"))
    total_tokens = _safe_int(data.get("total_tokens")) or input_tokens + output_tokens
    return {
        "fallback": bool(data.get("fallback", False)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "model_calls": _safe_int(data.get("model_calls")),
    }


def aggregate_usage(runs: list[Any]) -> dict[str, Any]:
    total = zero_usage()
    usage_items = []
    for run in runs:
        usage = normalize_usage(getattr(run, "usage", None))
        usage_items.append(usage)
        total["input_tokens"] += usage["input_tokens"]
        total["output_tokens"] += usage["output_tokens"]
        total["total_tokens"] += usage["total_tokens"]
        total["model_calls"] += usage["model_calls"]
    total["fallback"] = bool(usage_items) and all(item["fallback"] for item in usage_items)
    return total


def humanize_event(event_type: str | None) -> str:
    if not event_type:
        return "任务状态更新"
    return EVENT_PHRASES.get(str(event_type), "任务状态更新")


def _dt(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _seconds_between(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def _latest_error(runs: list[Any]) -> dict:
    for run in runs:
        error = getattr(run, "error", None)
        if isinstance(error, dict) and error:
            return error
    return {}


def _failure_layers(runs: list[Any]) -> dict:
    for run in runs:
        metadata = getattr(run, "metadata", None)
        if isinstance(metadata, dict) and isinstance(metadata.get("failure_layers"), dict):
            return metadata["failure_layers"]
    return {}


def build_task_summary(
    task: Any,
    runs: list[Any],
    approvals: list[Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    latest_run = runs[0] if runs else None
    pending_approvals = [item for item in approvals if getattr(item, "status", None) == "pending"]
    latest_error = _latest_error(runs)
    failure_layers = _failure_layers(runs)
    primary_failure = failure_layers.get("business") or failure_layers.get("runtime") or latest_error
    started_at = _dt(getattr(task, "started_at", None)) or _dt(getattr(latest_run, "started_at", None))
    finished_at = _dt(getattr(task, "finished_at", None)) or _dt(getattr(latest_run, "finished_at", None))
    status = getattr(task, "status", None)
    is_terminal = status in TERMINAL_TASK_STATUSES
    needs_clarification = status == "pending_clarification"
    duration_end = finished_at
    if started_at and not is_terminal:
        duration_end = now or datetime.now(tz=started_at.tzinfo)
    return {
        "status": status,
        "progress": _safe_int(getattr(task, "progress", 0)),
        "assigned_agent_key": getattr(task, "assigned_agent_key", None),
        "is_terminal": is_terminal,
        "duration_seconds": _seconds_between(started_at, duration_end),
        "token_usage": aggregate_usage(runs),
        "pending_approvals": len(pending_approvals),
        "needs_clarification": needs_clarification,
        "has_error": bool(latest_error),
        "latest_error": latest_error,
        "failure_layers": failure_layers,
        "primary_failure": primary_failure,
        "latest_run_status": getattr(latest_run, "status", None) if latest_run else None,
        "latest_run_id": getattr(latest_run, "run_id", None) if latest_run else None,
        "issues_count": len(pending_approvals) + (1 if latest_error else 0) + (1 if needs_clarification else 0),
    }
