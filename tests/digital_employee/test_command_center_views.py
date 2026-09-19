# coding=utf-8
import asyncio
import inspect
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.command_center import views  # noqa: E402


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
            "payload": {
                "secret": "hidden",
                "targets": ["db01", "db02"],
                "options": {"force": True},
                "clarifications": [{"question": "目标？", "answer": "credential-like-secret"}],
            },
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
        "clarifications": {"kind": "list", "count": 1},
    }
    assert out["result_summary"] == {"present": True, "length": 30}
    assert out["title"] == "Restart database"
    assert "clarifications" not in out
    assert "credential-like-secret" not in str(out)


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
            "authorization": {"source": "aioe_platform_role", "rbac_role_id": "role-1"},
            "filesystem": {"write": "deny", "paths": ["/tmp/a"]},
        },
        usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "model_calls": 1},
        cost={},
        error={"message": "needs approval"},
        tool_calls=[
            {"tool_name": "restart_service", "status": "success", "data": {"action": "restart_service"}},
            {"tool_name": None, "status": "success", "data": {"playbook": "incident_playbook"}},
            {"tool_name": None, "status": "started", "data": {"tool_call_name": "aioe_cli"}},
        ],
        resource_accesses=[{"kind": "host", "name": "web-01", "action": "read"}],
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
    assert out["policy_summary"]["authorization"] == {
        "source": "aioe_platform_role",
        "rbac_role_id": "role-1",
    }
    assert out["usage"]["total_tokens"] == 120
    assert out["cost"] == {}
    assert out["error"]["message"] == "needs approval"
    assert out["tool_calls"] == [
        {"name": "restart_service", "status": "success", "kind": "action"},
        {"name": "incident_playbook", "status": "success", "kind": "playbook"},
        {"name": "aioe_cli", "status": "started", "kind": "tool"},
    ]
    assert out["resource_accesses"] == [{"kind": "host", "name": "web-01", "action": "read"}]


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


def test_task_detail_dto_exposes_rich_content_and_safe_attachment_refs_only():
    task = SimpleNamespace(
        json={
            "id": "task-1",
            "ctime": "2026-06-29 13:00:00",
            "utime": "2026-06-29 13:01:00",
            "title": "拓扑核对",
            "description": "请核对图片中的拓扑",
            "source": "manual",
            "priority": 3,
            "status": "pending_dispatch",
            "progress": 0,
            "required_capabilities": [],
            "payload": {
                "content_html": "<p>请核对 <strong>图片</strong> 中的拓扑</p>",
                "attachments": [
                    {
                        "file_id": "attach-1.png",
                        "filename": "attach-1.png",
                        "size": 128,
                        "mime_type": "image/png",
                        "kind": "image",
                        "url": "/command_center/tasks/attachments/attach-1.png",
                    }
                ],
                "secret": "must-not-leak",
            },
            "requested_agent_id": None,
            "assigned_agent_id": None,
            "assigned_agent_key": None,
            "dispatch_status": "pending",
            "dispatch_reason": None,
            "run_id": None,
            "result": None,
            "room_id": None,
            "creator_id": "user-1",
            "started_at": None,
            "finished_at": None,
        }
    )

    out = views._task_detail_json(task)

    assert out["content_html"] == "<p>请核对 <strong>图片</strong> 中的拓扑</p>"
    assert out["attachments"] == task.json["payload"]["attachments"]
    assert "must-not-leak" not in str(out)
    assert "payload" not in out


def test_task_attachment_upload_scopes_storage_to_tenant_and_returns_safe_ref(monkeypatch):
    uploaded = SimpleNamespace(
        name="../../diagram.png",
        filename="../../diagram.png",
        body=b"png-bytes",
        type="image/png",
    )
    calls = []

    class Files:
        def getlist(self, key):
            assert key == "file"
            return [uploaded]

    async def fake_upload(content, bucket, file_name, prefix, content_type=None):
        calls.append((content, bucket, file_name, prefix, content_type))
        return f"/files/{bucket}/{prefix}/{file_name}"

    monkeypatch.setattr(views.uuid, "uuid4", lambda: "attach-1")
    monkeypatch.setattr(views, "upload_to_s3", fake_upload)
    monkeypatch.setattr(views, "response_ok", lambda data: data)
    request = SimpleNamespace(
        files=Files(),
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
    )

    out = asyncio.run(inspect.unwrap(views.upload_task_attachments)(request))

    assert calls == [(b"png-bytes", "user", "attach-1.png", "command_center/tenant-1", "image/png")]
    assert out["files"] == [
        {
            "file_id": "attach-1.png",
            "filename": "diagram.png",
            "size": 9,
            "mime_type": "image/png",
            "kind": "image",
            "url": "/command_center/tasks/attachments/attach-1.png",
        }
    ]


def test_task_attachment_download_scopes_lookup_to_current_tenant(monkeypatch):
    calls = []

    async def fake_get(bucket, file_name, prefix):
        calls.append((bucket, file_name, prefix))
        return b"file-bytes"

    monkeypatch.setattr(views, "get_from_s3", fake_get)
    monkeypatch.setattr(
        views.response,
        "raw",
        lambda content, **kwargs: {"content": content, **kwargs},
    )
    request = SimpleNamespace(
        args={"download_name": "diagram.png"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
    )

    out = asyncio.run(inspect.unwrap(views.download_task_attachment)(request, "attach-1.png"))

    assert calls == [("user", "attach-1.png", "command_center/tenant-1")]
    assert out["content"] == b"file-bytes"
    assert out["headers"]["Content-Type"] == "image/png"
    assert out["headers"]["Content-Disposition"].startswith("inline;")


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
    assert event_out["phrase"] == "等待人工审批"


def test_task_human_records_exclude_pending_approvals_and_process_events():
    def approval(approval_id, status, ctime):
        return SimpleNamespace(
            id=approval_id,
            status=status,
            trigger_type="runtime",
            risk_level="high",
            impacted_resources=[{"kind": "service", "name": "api"}],
            requested_action={"action": "restart_service"},
            rollback_plan="rollback",
            requester_id="requester-1",
            approver_id="operator-1" if status != "pending" else None,
            decided_at=ctime if status != "pending" else None,
            expires_at=None,
            decision_comment="done" if status != "pending" else None,
            metadata={"secret": "must not leak"},
            json={
                "ctime": ctime,
                "utime": ctime,
                "task_id": "task-1",
                "run_id": "run-1",
                "employee_id": "employee-1",
                "decided_at": ctime if status != "pending" else None,
                "expires_at": None,
            },
        )

    def event(event_id, event_type, ctime):
        return SimpleNamespace(
            id=event_id,
            event_type=event_type,
            status="completed",
            progress=100,
            agent_key="ops-agent",
            message="human action",
            metadata={"secret": "must not leak"},
            operator_id="operator-1",
            json={"ctime": ctime, "utime": ctime, "task_id": "task-1"},
        )

    records = views._task_human_records(
        [
            approval("approval-pending", "pending", "2026-06-29 13:00:00"),
            approval("approval-done", "approved", "2026-06-29 13:02:00"),
        ],
        [
            event("event-progress", "task_progress_updated", "2026-06-29 13:01:00"),
            event("event-answered", "clarification_answered", "2026-06-29 13:03:00"),
        ],
    )

    assert [record["id"] for record in records] == [
        "approval:approval-done",
        "event:event-answered",
    ]
    assert [record["record_type"] for record in records] == ["approval", "event"]
    assert all("metadata" not in record for record in records)


def test_task_stream_event_dto_exposes_one_bounded_render_item():
    event_out = views._task_stream_event_json(
        {
            "task_id": "task-1",
            "run_id": "run-1",
            "ctime": None,
            "utime": None,
            "status": "completed",
            "progress": 100,
            "agent_key": "ops-agent",
            "operator_id": "system",
            "message": "finished",
            "message_length": views.PROCESS_STREAM_MAX_LENGTH + 1,
            "event_count": 23_151,
        }
    )

    assert event_out["id"] == "runtime-stream:run-1"
    assert event_out["event_type"] == "runtime_stream"
    assert event_out["message"] == "finished"
    assert event_out["event_count"] == 23_151
    assert event_out["truncated"] is True
    assert event_out["metadata_summary"] == {"run_id": "run-1"}


def test_task_detail_defers_process_and_tool_history_to_lazy_endpoints():
    detail_source = inspect.getsource(inspect.unwrap(views.task_detail))
    process_source = inspect.getsource(inspect.unwrap(views.task_process))
    process_query_source = inspect.getsource(views._task_process_events)

    assert "CommandTaskEvent" in detail_source
    assert "event_type__in=HUMAN_HANDLING_EVENT_TYPES" in detail_source
    assert "_task_human_records" in detail_source
    assert "_task_run_detail_json" not in detail_source
    assert 'status=COMMAND_APPROVAL_STATUS_PENDING' in detail_source
    assert "_task_process_events" in process_source
    assert "STRING_AGG" in process_query_source
    assert "PROCESS_STREAM_MAX_LENGTH" in process_query_source
    assert "metadata->>'run_id'" in process_query_source


def test_task_stats_aggregates_tenant_scoped_counts(monkeypatch):
    class Query:
        def __init__(self, value):
            self.value = value
            self.filters = []

        async def all(self):
            return self.value

        async def count(self):
            return self.value

    run_filters = []
    task_filters = []
    approval_filters = []

    class RunModel:
        @classmethod
        def filter(cls, **kwargs):
            run_filters.append(kwargs)
            return Query(
                [
                    SimpleNamespace(
                        usage={
                            "fallback": False,
                            "input_tokens": 7,
                            "output_tokens": 3,
                            "total_tokens": 10,
                            "model_calls": 1,
                        }
                    )
                ]
            )

    class TaskModel:
        @classmethod
        def filter(cls, **kwargs):
            task_filters.append(kwargs)
            return Query(2 if kwargs.get("status__in") else 1)

    class ApprovalModel:
        @classmethod
        def filter(cls, **kwargs):
            approval_filters.append(kwargs)
            return Query(3)

    monkeypatch.setattr(views, "CommandTaskRun", RunModel)
    monkeypatch.setattr(views, "CommandTask", TaskModel)
    monkeypatch.setattr(views, "CommandApproval", ApprovalModel)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    request = SimpleNamespace(ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}))
    out = asyncio.run(inspect.unwrap(views.task_stats)(request))

    assert out["token_usage"]["total_tokens"] == 10
    assert out["tasks"]["running"] == 2
    assert out["risks"] == {"pending_approvals": 3, "failed_tasks": 1}
    assert run_filters[0]["tenant_id"] == "tenant-1"
    assert run_filters[0]["deleted"] is False
    assert task_filters[0]["deleted"] is False
    assert approval_filters[0] == {"tenant_id": "tenant-1", "deleted": False, "status": "pending"}


def test_clarification_answer_api_scopes_tenant_and_enforces_owner_for_create_only_actor(monkeypatch):
    calls = []
    account = SimpleNamespace(id="creator-1", roles=[])
    request = SimpleNamespace(
        json={"id": "task-1", "answer": "集群 prod-a"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "account_info": account}),
    )
    task = SimpleNamespace(
        json={
            "id": "task-1",
            "status": "pending_dispatch",
            "payload": {
                "secret": "hidden-value",
                "clarifications": [{"question": "目标？", "answer": "集群 prod-a"}],
            },
            "result": "sensitive-result",
        }
    )

    async def answer_task(tenant_id, task_id, answer, operator_id, *, owner_only=False):
        calls.append((tenant_id, task_id, answer, operator_id, owner_only))
        return task

    monkeypatch.setattr(views, "account_has_permission", lambda actor, permission, tenant_id=None: False)
    monkeypatch.setattr(views, "answer_task_clarification", answer_task)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    out = asyncio.run(inspect.unwrap(views.answer_clarification)(request))

    assert out["id"] == "task-1"
    assert out["payload_summary"] == {
        "secret": {"kind": "value", "present": True},
        "clarifications": {"kind": "list", "count": 1},
    }
    assert out["result_summary"] == {"present": True, "length": 16}
    assert "payload" not in out
    assert "result" not in out
    assert "hidden-value" not in str(out)
    assert "集群 prod-a" not in str(out)
    assert calls == [("tenant-1", "task-1", "集群 prod-a", "creator-1", True)]


def test_clarification_answer_api_allows_update_operator_without_ownership_check(monkeypatch):
    calls = []
    account = SimpleNamespace(id="operator-1", roles=[])
    request = SimpleNamespace(
        json={"id": "task-1", "answer": "集群 prod-a"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "account_info": account}),
    )
    task = SimpleNamespace(json={"id": "task-1", "status": "pending_dispatch"})

    async def answer_task(tenant_id, task_id, answer, operator_id, *, owner_only=False):
        calls.append(owner_only)
        return task

    monkeypatch.setattr(views, "account_has_permission", lambda actor, permission, tenant_id=None: True)
    monkeypatch.setattr(views, "answer_task_clarification", answer_task)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    asyncio.run(inspect.unwrap(views.answer_clarification)(request))

    assert calls == [False]
    assert 'requires_permission("command_center:c", "command_center:u")' in inspect.getsource(views.answer_clarification)


def test_command_task_openapi_enums_include_clarification_state():
    assert "pending_clarification" in views.TASK_SCHEMA["properties"]["status"]["enum"]
    assert "needs_clarification" in views.TASK_SCHEMA["properties"]["dispatch_status"]["enum"]
    assert "payload" not in views.TASK_SCHEMA["properties"]
    assert "payload_summary" in views.TASK_SCHEMA["properties"]
    assert "result_summary" in views.TASK_SCHEMA["properties"]
    assert views.TASK_PROGRESS_WRITE_STATUSES == {"running", "completed", "failed", "cancelled"}
    assert "pending_clarification" not in views.TASK_PROGRESS_WRITE_STATUS_ENUM
    assert '"enum": TASK_PROGRESS_WRITE_STATUS_ENUM' in inspect.getsource(views.task_progress)


def test_task_progress_rejects_pending_clarification_before_loading_or_updating_task(monkeypatch):
    calls = []

    class TaskModel:
        @classmethod
        async def get_or_none(cls, **kwargs):
            calls.append(("load", kwargs))
            return None

    async def update_progress(task, data, operator_id=None):
        calls.append(("update", data))
        return task

    request = SimpleNamespace(
        json={"id": "task-1", "status": "pending_clarification"},
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-1",
                "account_info": SimpleNamespace(id="operator-1"),
            }
        ),
    )
    monkeypatch.setattr(views, "CommandTask", TaskModel)
    monkeypatch.setattr(views, "update_task_progress", update_progress)

    with pytest.raises(views.CustomError) as exc_info:
        asyncio.run(inspect.unwrap(views.task_progress)(request))

    assert exc_info.value.enum is views.ErrorCode.PARAM_VALUE_ERROR
    assert calls == []


def test_retry_failed_task_scopes_claim_and_schedules_background_run(monkeypatch):
    calls = []
    task = SimpleNamespace(
        id="task-1",
        status="failed",
        run_id="run-1",
        json={"id": "task-1", "status": "failed", "run_id": "run-1"},
    )
    run = SimpleNamespace(id="row-1", run_id="run-1", status="failed")
    employee = SimpleNamespace(id="employee-1")

    class TaskModel:
        @classmethod
        async def get_or_none(cls, **kwargs):
            calls.append(("task", kwargs))
            return task

    class RunQuery:
        async def first(self):
            return run

    class RunModel:
        @classmethod
        def filter(cls, **kwargs):
            calls.append(("run", kwargs))
            return RunQuery()

    async def prepare(run_arg, operator_id):
        calls.append(("prepare", run_arg, operator_id))
        task.status = "dispatched"
        task.run_id = None
        task.json = {"id": task.id, "status": task.status, "run_id": task.run_id}
        return task, employee

    def schedule(run_arg, task_arg, employee_arg, operator_id):
        calls.append(("schedule", run_arg, task_arg, employee_arg, operator_id))

    request = SimpleNamespace(
        json={"id": "task-1"},
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-1",
                "account_info": SimpleNamespace(id="operator-1"),
            }
        ),
    )
    monkeypatch.setattr(views, "CommandTask", TaskModel)
    monkeypatch.setattr(views, "CommandTaskRun", RunModel)
    monkeypatch.setattr(views, "prepare_run_retry", prepare)
    monkeypatch.setattr(views, "schedule_prepared_run_retry", schedule)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    out = asyncio.run(inspect.unwrap(views.retry_failed_task)(request))

    assert out["status"] == "dispatched"
    assert calls == [
        ("task", {"id": "task-1", "tenant_id": "tenant-1", "deleted": False}),
        (
            "run",
            {
                "task_id": "task-1",
                "tenant_id": "tenant-1",
                "run_id": "run-1",
                "deleted": False,
            },
        ),
        ("prepare", run, "operator-1"),
        ("schedule", run, task, employee, "operator-1"),
    ]
