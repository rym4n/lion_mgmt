# coding=utf-8
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.command_center.summary import aggregate_usage, build_task_summary, humanize_event, normalize_usage  # noqa: E402


def test_humanize_event_known_and_unknown():
    assert humanize_event("runtime_tool_call") == "数字员工正在执行工具"
    assert humanize_event("approval_required") == "等待人工审批"
    assert humanize_event("clarification_requested") == "等待用户补充任务信息"
    assert humanize_event("clarification_answered") == "用户已补充任务信息"
    assert humanize_event("custom") == "任务状态更新"


def test_normalize_usage_defaults_missing_fields_to_zero():
    usage = normalize_usage({"input_tokens": "3", "output_tokens": 2})
    assert usage == {
        "fallback": False,
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
        "model_calls": 0,
    }


def test_aggregate_usage_sums_runs():
    runs = [
        SimpleNamespace(usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "model_calls": 1}),
        SimpleNamespace(usage={"input_tokens": 2, "output_tokens": 3, "model_calls": 1}),
    ]
    assert aggregate_usage(runs) == {
        "fallback": False,
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
        "model_calls": 2,
    }


def test_build_task_summary_combines_progress_usage_and_issues():
    start = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 1, 9, 2, 3, tzinfo=timezone.utc)
    task = SimpleNamespace(
        status="failed",
        progress=70,
        assigned_agent_key="ops",
        started_at=start,
        finished_at=end,
    )
    runs = [
        SimpleNamespace(
            status="failed",
            run_id="run-1",
            usage={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14, "model_calls": 1},
            error={"message": "boom"},
            started_at=start,
            finished_at=end,
        )
    ]
    approvals = [SimpleNamespace(status="pending"), SimpleNamespace(status="approved")]

    summary = build_task_summary(task, runs, approvals)

    assert summary["is_terminal"] is True
    assert summary["duration_seconds"] == 123
    assert summary["token_usage"]["total_tokens"] == 14
    assert summary["pending_approvals"] == 1
    assert summary["has_error"] is True
    assert summary["issues_count"] == 2


def test_build_task_summary_uses_server_clock_for_live_duration():
    start = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
    stale_finish = datetime(2026, 7, 1, 9, 0, 10, tzinfo=timezone.utc)
    now = datetime(2026, 7, 1, 9, 1, 5, tzinfo=timezone.utc)
    task = SimpleNamespace(
        status="running",
        progress=30,
        assigned_agent_key="ops",
        started_at=start,
        finished_at=stale_finish,
    )

    summary = build_task_summary(task, [], [], now=now)

    assert summary["is_terminal"] is False
    assert summary["duration_seconds"] == 65


def test_build_task_summary_counts_pending_clarification_as_attention_issue():
    task = SimpleNamespace(
        status="pending_clarification",
        progress=0,
        assigned_agent_key=None,
        started_at=None,
        finished_at=None,
    )

    summary = build_task_summary(task, [], [])

    assert summary["needs_clarification"] is True
    assert summary["issues_count"] == 1
