# coding=utf-8
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys


def _load_module(name: str, relative_path: str):
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    path = root / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


command_center = _load_module("models.command_center_model_contract_test", "models/command_center.py")
utils = _load_module("libs.utils_model_contract_test", "libs/utils.py")


def test_command_run_and_approval_status_constants_are_flat_contracts():
    assert command_center.COMMAND_RUNTIME_LION_AGENT == "lion_agent"
    assert command_center.COMMAND_TASK_STATUS_PENDING_APPROVAL == "pending_approval"
    assert command_center.COMMAND_RUN_STATUS_WAITING_APPROVAL == "waiting_approval"
    assert command_center.COMMAND_RUN_STATUS_CONTINUATION_READY == "continuation_ready"
    assert command_center.COMMAND_RUN_STATUS_REJECTED == "rejected"
    assert command_center.COMMAND_APPROVAL_STATUS_APPROVED_READ_ONLY == "approved_read_only"
    assert command_center.COMMAND_TASK_TERMINAL_STATUSES == {
        "completed",
        "failed",
        "cancelled",
        "handed_off",
    }


def test_command_task_run_json_contains_audit_and_runtime_snapshot_fields():
    started_at = datetime(2026, 6, 29, 13, 0, tzinfo=timezone.utc)
    run = command_center.CommandTaskRun(
        id="run-row-1",
        tenant_id="tenant-1",
        run_id="run-1",
        session_id="session-1",
        runtime="lion_agent",
        status="waiting_approval",
        lease_owner="worker-1",
        workspace_etag="etag-1",
        manifest_snapshot={"employee": {"id": "employee-1"}},
        policy_snapshot={"approval": {"write_file": "approval_required"}},
        tool_calls=[{"name": "write_file"}],
        resource_accesses=[{"kind": "asset", "id": "db01"}],
        usage={"tokens": 10},
        cost={"usd": 0.01},
        transcript_ref="transcript:event-1",
        evidence_refs=["event:approval-required"],
        error={},
        started_at=started_at,
        metadata={"continuation_model": "lion_mgmt_held_continuation"},
    )
    run.task_id = "task-1"
    run.employee_id = "employee-1"

    out = run.json

    assert out["task_id"] == "task-1"
    assert out["employee_id"] == "employee-1"
    assert out["tenant_id"] == "tenant-1"
    assert out["runtime"] == "lion_agent"
    assert out["status"] == "waiting_approval"
    assert out["workspace_etag"] == "etag-1"
    assert out["manifest_snapshot"]["employee"]["id"] == "employee-1"
    assert out["policy_snapshot"]["approval"]["write_file"] == "approval_required"
    assert out["tool_calls"][0]["name"] == "write_file"
    assert out["resource_accesses"][0]["id"] == "db01"
    assert out["transcript_ref"] == "transcript:event-1"
    assert out["evidence_refs"] == ["event:approval-required"]
    assert out["metadata"]["continuation_model"] == "lion_mgmt_held_continuation"
    assert out["started_at"] == utils.strip_timezone_to_seconds(started_at)


def test_command_approval_json_keeps_command_center_source_of_truth_shape():
    decided_at = datetime(2026, 6, 29, 13, 10, tzinfo=timezone.utc)
    approval = command_center.CommandApproval(
        id="approval-1",
        tenant_id="tenant-1",
        status="approved_read_only",
        trigger_type="runtime_tool",
        risk_level="high",
        policy_snapshot={"filesystem": {"write": "approval_required"}},
        impacted_resources=[{"kind": "file", "path": "/tmp/a"}],
        requested_action={"tool": "write_file", "path": "/tmp/a"},
        rollback_plan="restore backup",
        requester_id="user-1",
        approver_id="manager-1",
        decided_at=decided_at,
        decision_comment="read only first",
        metadata={"visible_inbox": "approvement_adapter"},
    )
    approval.task_id = "task-1"
    approval.run_id = "run-row-1"
    approval.employee_id = "employee-1"

    out = approval.json

    assert out["task_id"] == "task-1"
    assert out["run_id"] == "run-row-1"
    assert out["employee_id"] == "employee-1"
    assert out["status"] == "approved_read_only"
    assert out["trigger_type"] == "runtime_tool"
    assert out["risk_level"] == "high"
    assert out["policy_snapshot"]["filesystem"]["write"] == "approval_required"
    assert out["impacted_resources"][0]["path"] == "/tmp/a"
    assert out["requested_action"]["tool"] == "write_file"
    assert out["rollback_plan"] == "restore backup"
    assert out["requester_id"] == "user-1"
    assert out["approver_id"] == "manager-1"
    assert out["decided_at"] == utils.strip_timezone_to_seconds(decided_at)
    assert out["metadata"]["visible_inbox"] == "approvement_adapter"


def test_existing_command_task_json_remains_backward_compatible():
    task = command_center.CommandTask(
        id="task-1",
        tenant_id="tenant-1",
        title="巡检",
        description="检查磁盘",
        source="manual",
        priority=3,
        status="pending_dispatch",
        progress=0,
        required_capabilities=[],
        payload={},
        requested_agent_id=None,
        assigned_agent_id=None,
        assigned_agent_key=None,
        dispatch_status="pending",
        dispatch_reason=None,
        run_id=None,
        result=None,
        room_id=None,
        creator_id=None,
    )

    out = task.json

    assert set(out) == {
        "id",
        "ctime",
        "utime",
        "title",
        "description",
        "source",
        "priority",
        "status",
        "progress",
        "required_capabilities",
        "payload",
        "requested_agent_id",
        "assigned_agent_id",
        "assigned_agent_key",
        "dispatch_status",
        "dispatch_reason",
        "run_id",
        "result",
        "room_id",
        "creator_id",
        "started_at",
        "finished_at",
    }
