# coding=utf-8
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.command_center import views


def test_task_read_dto_summarizes_payload_and_result_without_raw_values():
    task = SimpleNamespace(
        json={
            "id": "task-1",
            "ctime": "2026-06-29 13:00:00",
            "utime": "2026-06-29 13:01:00",
            "title": "Restart database",
            "description": "recover service",
            "source": "manual",
            "priority": 1,
            "status": "pending_approval",
            "progress": 30,
            "required_capabilities": ["ops"],
            "payload": {"secret": "hidden", "targets": ["db01", "db02"], "options": {"force": True}},
            "requested_agent_id": "ops-agent",
            "assigned_agent_id": "employee-1",
            "assigned_agent_key": "ops-agent",
            "dispatch_status": "assigned",
            "dispatch_reason": "matched",
            "run_id": "run-1",
            "result": "sensitive execution transcript",
            "room_id": "room-1",
            "creator_id": "user-1",
            "started_at": None,
            "finished_at": None,
        }
    )

    out = views._task_detail_json(task)

    assert "payload" not in out
    assert "result" not in out
    assert out["payload_summary"] == {
        "secret": {"kind": "value", "present": True},
        "targets": {"kind": "list", "count": 2},
        "options": {"kind": "object", "count": 1},
    }
    assert out["result_summary"] == {"present": True, "length": 30}
    assert out["title"] == "Restart database"


def test_task_detail_run_dto_exposes_summaries_without_raw_snapshots():
    run = SimpleNamespace(
        id="row-1",
        json={
            "ctime": "2026-06-29 13:00:00",
            "utime": "2026-06-29 13:01:00",
            "task_id": "task-1",
            "employee_id": "employee-1",
            "heartbeat_at": "2026-06-29 13:01:00",
            "started_at": "2026-06-29 13:00:00",
            "finished_at": None,
        },
        run_id="run-1",
        runtime="lion_agent",
        status="waiting_approval",
        workspace_etag="etag-1",
        manifest_snapshot={
            "employee": {"id": "employee-1", "name": "Ops", "agent_key": "ops-agent", "secret": "hidden"},
            "skills": [{"skill_key": "write_file", "prompt": "internal"}],
            "workspace_etag": "etag-1",
        },
        policy_snapshot={
            "approval": {"default": "approval_required", "skills": {"write_file": "approval_required"}},
            "resource_scope": {"assets": ["db01", "db02"], "filesystem": {"paths": ["/tmp/a"]}},
            "filesystem": {"write": "deny", "paths": ["/tmp/a"]},
        },
    )

    out = views._task_run_detail_json(run)

    assert "manifest_snapshot" not in out
    assert "policy_snapshot" not in out
    assert "metadata" not in out
    assert out["manifest_summary"] == {
        "employee": {"id": "employee-1", "name": "Ops", "agent_key": "ops-agent", "role_key": None},
        "skills_count": 1,
        "workspace_etag": "etag-1",
    }
    assert out["policy_summary"]["approval_default"] == "approval_required"
    assert out["policy_summary"]["approval_skills"] == {"write_file": "approval_required"}
    assert out["policy_summary"]["resource_scope"] == {
        "assets": {"kind": "list", "count": 2},
        "filesystem": {"kind": "object", "count": 1},
    }


def test_command_center_read_approval_dto_allowlists_operator_fields():
    approval = SimpleNamespace(
        id="approval-1",
        json={
            "ctime": "2026-06-29 13:00:00",
            "utime": "2026-06-29 13:01:00",
            "task_id": "task-1",
            "run_id": "row-1",
            "employee_id": "employee-1",
            "decided_at": None,
            "expires_at": None,
        },
        status="pending",
        trigger_type="runtime_tool",
        risk_level="high",
        impacted_resources=[{"kind": "file", "path": "/tmp/secret", "scope": {"paths": ["/tmp/secret"]}}],
        requested_action={"tool": "write_file", "path": "/tmp/secret", "content": "secret", "risk_level": "high"},
        rollback_plan="restore from snapshot",
        requester_id="user-1",
        approver_id=None,
        decision_comment=None,
        metadata={"needs_info": {"requested": True, "comment": "补充目标机器", "requested_by": "approver-1", "requested_at": "2026-06-29T13:01:00+00:00"}},
    )
    approval_out = views._task_approval_detail_json(approval)

    assert "policy_snapshot" not in approval_out
    assert "metadata" not in approval_out
    assert approval_out["requested_action"] == {"tool": "write_file", "risk_level": "high"}
    assert approval_out["impacted_resources"] == [
        {"kind": "file", "scope": {"paths": {"kind": "list", "count": 1}}}
    ]
    assert approval_out["needs_info"]["comment"] == "补充目标机器"
    assert "approve" in approval_out["available_decisions"]


def test_task_detail_event_dto_exposes_metadata_summary_only():
    event = SimpleNamespace(
        id="event-1",
        json={
            "ctime": "2026-06-29 13:00:00",
            "utime": "2026-06-29 13:01:00",
            "task_id": "task-1",
        },
        event_type="approval_required",
        status="pending_approval",
        progress=10,
        agent_key="ops-agent",
        message="needs approval",
        metadata={"run_id": "run-1", "approval_id": "approval-1", "runtime_event": "tool_call", "payload": {"secret": True}},
        operator_id="system",
    )

    event_out = views._task_event_detail_json(event)

    assert "metadata" not in event_out
    assert event_out["metadata_summary"] == {
        "run_id": "run-1",
        "approval_id": "approval-1",
        "runtime_event": "tool_call",
    }
