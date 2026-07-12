# coding=utf-8
import asyncio
from datetime import timezone
from datetime import datetime
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types

import pytest

RECORDED_EVENTS = []
UPDATED_PROGRESS = []
SAVED = []
CREATED_APPROVEMENTS = []
STUB_MODULES = [
    "core",
    "core.error_code",
    "libs",
    "libs.error_code",
    "libs.error_code.exception",
    "libs.security",
    "libs.security.authorization",
    "models",
    "models.command_center",
    "sanic",
    "sanic.log",
    "settings",
    "tortoise",
    "tortoise.transactions",
    "services",
    "services.command_center",
    "services.command_center.manager",
    "services.command_center.approval_contract",
    "services.command_center.approval_service",
    "services.command_center.run_manager",
]
_MISSING_MODULE = object()


@pytest.fixture(autouse=True)
def _cleanup_stub_modules():
    original_modules = {name: sys.modules.get(name, _MISSING_MODULE) for name in STUB_MODULES}
    yield
    for name in STUB_MODULES:
        original = original_modules[name]
        if original is _MISSING_MODULE:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


def _install_stubs():
    RECORDED_EVENTS.clear()
    UPDATED_PROGRESS.clear()
    SAVED.clear()
    CREATED_APPROVEMENTS.clear()
    for name in STUB_MODULES:
        sys.modules.pop(name, None)

    core = types.ModuleType("core")
    sys.modules["core"] = core
    error_code = types.ModuleType("core.error_code")
    error_code.ErrorCode = SimpleNamespace(PARAM_VALUE_ERROR="PARAM_VALUE_ERROR", NOT_FOUND="NOT_FOUND", APPROVEMENT_STATUS_ERROR="APPROVEMENT_STATUS_ERROR")
    sys.modules["core.error_code"] = error_code
    libs = types.ModuleType("libs")
    libs.__path__ = []
    sys.modules["libs"] = libs
    libs_error = types.ModuleType("libs.error_code")
    libs_error.__path__ = []
    sys.modules["libs.error_code"] = libs_error
    exception = types.ModuleType("libs.error_code.exception")

    class CustomError(Exception):
        pass

    exception.CustomError = CustomError
    sys.modules["libs.error_code.exception"] = exception
    libs_security = types.ModuleType("libs.security")
    libs_security.__path__ = []
    sys.modules["libs.security"] = libs_security
    authorization = types.ModuleType("libs.security.authorization")
    authorization.check_wildcard = lambda owned, needed: owned in {needed, "*:*"} or owned.endswith(":*") and needed.startswith(owned[:-1])
    sys.modules["libs.security.authorization"] = authorization

    sanic = types.ModuleType("sanic")
    sanic.__path__ = []
    sys.modules["sanic"] = sanic
    sanic_log = types.ModuleType("sanic.log")
    sanic_log.logger = SimpleNamespace(warning=lambda *args, **kwargs: None)
    sys.modules["sanic.log"] = sanic_log

    tortoise = types.ModuleType("tortoise")
    transactions = types.ModuleType("tortoise.transactions")

    class Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    transactions.in_transaction = lambda: Tx()
    sys.modules["tortoise"] = tortoise
    sys.modules["tortoise.transactions"] = transactions

    models = types.ModuleType("models")
    sys.modules["models"] = models
    command_center = types.ModuleType("models.command_center")
    command_center.COMMAND_APPROVAL_STATUS_APPROVED = "approved"
    command_center.COMMAND_APPROVAL_STATUS_APPROVED_READ_ONLY = "approved_read_only"
    command_center.COMMAND_APPROVAL_STATUS_PENDING = "pending"
    command_center.COMMAND_RUN_STATUS_CHECKED_OUT = "checked_out"
    command_center.COMMAND_RUN_STATUS_FAILED = "failed"
    command_center.COMMAND_RUN_STATUS_RUNNING = "running"
    command_center.COMMAND_RUN_STATUS_WAITING_APPROVAL = "waiting_approval"
    command_center.COMMAND_RUN_TERMINAL_STATUSES = {
        "completed",
        "failed",
        "cancelled",
        "rejected",
        "stale",
        "handed_off",
    }
    command_center.COMMAND_TASK_STATUS_FAILED = "failed"
    command_center.COMMAND_TASK_STATUS_DISPATCHED = "dispatched"
    command_center.COMMAND_TASK_STATUS_PENDING_APPROVAL = "pending_approval"
    command_center.COMMAND_TASK_STATUS_RUNNING = "running"
    command_center.COMMAND_TASK_TERMINAL_STATUSES = {
        "completed",
        "failed",
        "cancelled",
        "handed_off",
    }

    class Query:
        approval = None

        def __init__(self, approval=None):
            self.approvals = list(approval) if isinstance(approval, list) else ([approval] if approval is not None else [])

        def select_for_update(self):
            return self

        def using_db(self, connection):
            return self

        def filter(self, **kwargs):
            return self

        def prefetch_related(self, *args):
            return self

        async def first(self):
            return self.approvals[0] if self.approvals else None

        async def count(self):
            return len(self.approvals)

        def order_by(self, *args):
            return self

        def offset(self, value):
            return self

        def limit(self, value):
            return self

        async def all(self):
            return self.approvals

    class CommandTask(SimpleNamespace):
        records = None

        @classmethod
        def filter(cls, **kwargs):
            return Query(cls.records)

        async def save(self, using_db=None, update_fields=None):
            SAVED.append(("task", update_fields, self.status))

    class CommandTaskRun(SimpleNamespace):
        records = None

        @classmethod
        def filter(cls, **kwargs):
            return Query(cls.records)

        async def save(self, using_db=None, update_fields=None):
            SAVED.append(("run", update_fields, self.status))

    class CommandApproval(SimpleNamespace):
        created = []
        active = None
        records = None

        @classmethod
        async def create(cls, **kwargs):
            approval = cls(**kwargs)
            approval.id = kwargs.get("id", f"approval-{len(cls.created) + 1}")
            approval.save_calls = []
            cls.created.append(approval)
            return approval

        @classmethod
        def filter(cls, **kwargs):
            return Query(cls.records if cls.records is not None else cls.active)

        @classmethod
        async def get_or_none(cls, **kwargs):
            if cls.records is not None:
                for record in cls.records:
                    if record.id == kwargs.get("id") and record.tenant_id == kwargs.get("tenant_id"):
                        return record
                return None
            return cls.active

        async def save(self, using_db=None, update_fields=None):
            self.save_calls.append(update_fields)
            SAVED.append(("approval", update_fields, self.status))

    class Account(SimpleNamespace):
        records = None

        @classmethod
        def filter(cls, **kwargs):
            return Query(cls.records)

        @classmethod
        async def get_or_none(cls, **kwargs):
            for account in cls.records or []:
                if getattr(account, "id", None) == kwargs.get("id"):
                    return account
            return None

    class ApproverRelation:
        def __init__(self, approval):
            self.approval = approval

        async def add(self, *approvers):
            self.approval.added_approvers.extend(approvers)

        def __iter__(self):
            return iter(self.approval.added_approvers)

    class Approvement(SimpleNamespace):
        records = None

        @classmethod
        async def create(cls, **kwargs):
            approvement = cls(**kwargs)
            approvement.id = kwargs.get("id", f"approvement-{len(CREATED_APPROVEMENTS) + 1}")
            approvement.results = kwargs.get("results") or {}
            approvement.added_approvers = []
            approvement.approvers = ApproverRelation(approvement)
            approvement.save_calls = []
            CREATED_APPROVEMENTS.append(approvement)
            cls.records = [approvement]
            return approvement

        @classmethod
        def filter(cls, **kwargs):
            return Query(cls.records)

        async def save(self, update_fields=None):
            self.save_calls.append(update_fields)
            SAVED.append(("approvement", update_fields, self.status))

    Account.records = [
        Account(
            id="approver-1",
            username="approver",
            display_name="审批员",
            roles=[SimpleNamespace(permissions=["approve:u"])],
        )
    ]

    models.Account = Account
    models.Approvement = Approvement
    command_center.CommandTask = CommandTask
    command_center.CommandTaskRun = CommandTaskRun
    command_center.CommandApproval = CommandApproval
    sys.modules["models.command_center"] = command_center

    settings = types.ModuleType("settings")
    settings.TZ = timezone.utc
    sys.modules["settings"] = settings

    services = types.ModuleType("services")
    services.__path__ = []
    sys.modules["services"] = services
    command_center_pkg = types.ModuleType("services.command_center")
    command_center_pkg.__path__ = []
    sys.modules["services.command_center"] = command_center_pkg
    manager = types.ModuleType("services.command_center.manager")

    async def record_task_event(task, event_type, message=None, operator_id=None, metadata=None, using_db=None):
        RECORDED_EVENTS.append((event_type, message, operator_id, metadata or {}))
        return SimpleNamespace(event_type=event_type)

    async def update_task_progress(task, data, operator_id=None):
        UPDATED_PROGRESS.append((data, operator_id))
        if "status" in data:
            task.status = data["status"]
        return task

    async def apply_task_progress_in_transaction(
        connection,
        task,
        data,
        operator_id=None,
        *,
        locked_task=None,
        expected_run_id=None,
    ):
        if expected_run_id is not None and task.run_id != expected_run_id:
            return None
        old_status = task.status
        if "status" in data:
            task.status = data["status"]
        UPDATED_PROGRESS.append((data, operator_id))
        return task, None, old_status != task.status

    async def complete_task_progress_after_commit(task, transition, data):
        return transition[0]

    manager.record_task_event = record_task_event
    manager.update_task_progress = update_task_progress
    manager.apply_task_progress_in_transaction = apply_task_progress_in_transaction
    manager.complete_task_progress_after_commit = complete_task_progress_after_commit
    sys.modules["services.command_center.manager"] = manager

    approval_contract = types.ModuleType("services.command_center.approval_contract")
    approval_contract.APPROVEMENT_PRESENTATION_ADAPTER = "approvement_inbox_adapter"

    def approval_required_context(**kwargs):
        return kwargs

    transitions = {
        "approve": ("approved", "dispatched", "continuation_ready", True, "approval_decided", "continuation_started"),
        "read_only": ("approved_read_only", "dispatched", "continuation_ready", True, "approval_decided", "continuation_started"),
        "reject": ("rejected", "cancelled", "rejected", True, "approval_decided", None),
        "takeover": ("handed_off", "handed_off", "handed_off", True, "takeover", None),
        "needs_info": ("pending", "pending_approval", "waiting_approval", False, "approval_decided", None),
    }

    def approval_decision_transition(decision, context):
        approval_status, task_status, run_status, release_lease, event_type, next_event_type = transitions[decision]
        return {
            "decision": decision,
            "approval_status": approval_status,
            "task_status": task_status,
            "run_status": run_status,
            "release_lease": release_lease,
            "event_type": event_type,
            "next_event_type": next_event_type,
            "needs_info": decision == "needs_info",
            "approval_id": context["approval_id"],
            "run_id": context["run_id"],
            "policy_mutation": "read_only" if decision == "read_only" else None,
        }

    approval_contract.approval_required_context = approval_required_context
    approval_contract.approval_decision_transition = approval_decision_transition
    sys.modules["services.command_center.approval_contract"] = approval_contract


def _load_service():
    _install_stubs()
    path = Path(__file__).resolve().parents[2] / "services/command_center/approval_service.py"
    spec = importlib.util.spec_from_file_location("services.command_center.approval_service", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["services.command_center.approval_service"] = module
    spec.loader.exec_module(module)
    return module


class AwaitableValue:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        async def _inner():
            return self.value

        return _inner().__await__()


def _async_value(value):
    return AwaitableValue(value)


def _task_run_approval():
    task = sys.modules["models.command_center"].CommandTask(
        id="task-1",
        tenant_id="tenant-1",
        status="pending_approval",
        run_id="run-1",
    )
    run = sys.modules["models.command_center"].CommandTaskRun(
        id="run-row-1",
        tenant_id="tenant-1",
        run_id="run-1",
        session_id=None,
        status="waiting_approval",
        policy_snapshot={
            "approval": {"default": "approval_required"},
            "authorization": {"source": "aioe_platform_role", "rbac_role_id": "role-1"},
        },
        evidence_refs=[],
        metadata={},
        lease_owner="worker-1",
        lease_expires_at=None,
    )
    employee = SimpleNamespace(id="employee-1")
    approval = sys.modules["models.command_center"].CommandApproval(
        id="approval-1",
        tenant_id="tenant-1",
        status="pending",
        risk_level="high",
        trigger_type="runtime",
        policy_snapshot=run.policy_snapshot,
        requested_action={"tool": "write_file"},
        metadata={},
        employee_id="employee-1",
        task_id="task-1",
        save_calls=[],
    )
    approval.task = _async_value(task)
    approval.run = _async_value(run)
    approval.employee = _async_value(employee)
    run.employee = _async_value(employee)
    sys.modules["models.command_center"].CommandApproval.active = approval
    sys.modules["models.command_center"].CommandApproval.records = None
    sys.modules["models.command_center"].CommandTask.records = [task]
    sys.modules["models.command_center"].CommandTaskRun.records = [run]
    return task, run, approval


def test_policy_evaluation_supports_allow_approval_and_deny():
    service = _load_service()

    assert service.evaluate_policy({"approval": {"default": "allow"}}, {}).decision == "allow"
    assert service.evaluate_policy({"approval": {"default": "approval_required"}}, {}).requires_approval is True
    assert service.evaluate_policy({"approval": {"default": "deny"}}, {}).denied is True
    by_skill = service.evaluate_policy({"approval": {"skills": {"write_file": "approval_required"}}}, {"tool": "write_file"})
    assert by_skill.requires_approval is True


def test_create_approval_request_updates_run_task_and_event():
    service = _load_service()
    task, run, _approval = _task_run_approval()

    approval = asyncio.run(
        service.create_approval_request(
            task,
            run,
            trigger_type="pre_run",
            requested_action={"tool": "write_file"},
            risk_level="high",
            operator_id="requester-1",
            reason="策略要求执行前审批",
        )
    )

    assert approval.status == "pending"
    assert approval.metadata["visible_inbox"] == "approvement"
    assert approval.metadata["approval_kind"] == "agent"
    assert approval.metadata["approvement_id"] == "approvement-1"
    assert CREATED_APPROVEMENTS[0].params["approval_kind"] == "agent"
    assert CREATED_APPROVEMENTS[0].params["command_approval_id"] == approval.id
    assert [approver.id for approver in CREATED_APPROVEMENTS[0].added_approvers] == ["approver-1"]
    assert run.status == "waiting_approval"
    assert task.status == "pending_approval"
    assert RECORDED_EVENTS[-1][0] == "approval_required"


def test_pre_run_approval_does_not_reopen_cancelled_task():
    service = _load_service()
    task, run, _approval = _task_run_approval()
    task.status = "cancelled"
    run.status = "checked_out"
    run.policy_snapshot = {"approval": {"default": "approval_required"}}

    with pytest.raises(service.CustomError) as exc_info:
        asyncio.run(service.enforce_pre_run_policy(task, run, operator_id="operator-1"))

    assert exc_info.value.args == ("APPROVEMENT_STATUS_ERROR",)
    assert task.status == "cancelled"
    assert run.status == "checked_out"
    assert sys.modules["models.command_center"].CommandApproval.created == []
    assert CREATED_APPROVEMENTS == []
    assert SAVED == []
    assert RECORDED_EVENTS == []


def test_decide_approval_updates_linked_approvement_record():
    service = _load_service()
    task, run, approval = _task_run_approval()
    approvement = sys.modules["models"].Approvement(
        id="approvement-1",
        tenant_id="tenant-1",
        deleted=False,
        status=0,
        results={},
        added_approvers=[],
        save_calls=[],
    )
    approvement.approvers = []
    sys.modules["models"].Approvement.records = [approvement]
    approval.metadata = {"approvement_id": "approvement-1"}
    scheduled = []
    inbox_syncs = []
    service.schedule_approval_resume = lambda tenant_id, approval_id, operator_id=None: scheduled.append((tenant_id, approval_id, operator_id))
    service.schedule_approvement_decision_sync = lambda *args: inbox_syncs.append(args)

    decided = asyncio.run(service.decide_approval("tenant-1", "approval-1", "approve", "approver-1", "通过"))

    assert decided.status == "approved"
    assert task.status == "dispatched"
    assert run.status == "continuation_ready"
    assert approvement.status == 0
    assert approvement.results == {}
    assert scheduled == [("tenant-1", "approval-1", "approver-1")]
    assert inbox_syncs == [(approval, "approve", "approver-1", "通过")]


def test_decide_approval_schedules_resume_when_approvement_sync_fails(monkeypatch):
    service = _load_service()
    task, run, approval = _task_run_approval()
    schedule_order = []
    background_coroutines = []
    sync_calls = []

    async def fail_approvement_sync(*args, **kwargs):
        sync_calls.append((args, kwargs))
        raise RuntimeError("approvement unavailable")

    service.sync_command_approvement_decision = fail_approvement_sync
    service.schedule_approval_resume = lambda *args, **kwargs: schedule_order.append("resume")
    original_schedule_inbox_sync = service.schedule_approvement_decision_sync

    def schedule_inbox_sync(*args, **kwargs):
        schedule_order.append("inbox")
        return original_schedule_inbox_sync(*args, **kwargs)

    service.schedule_approvement_decision_sync = schedule_inbox_sync
    monkeypatch.setattr(
        service.asyncio,
        "create_task",
        lambda coro: background_coroutines.append(coro) or SimpleNamespace(),
    )

    decided = asyncio.run(service.decide_approval("tenant-1", "approval-1", "approve", "approver-1", "通过"))

    assert decided is approval
    assert decided.status == "approved"
    assert decided.metadata["decision"] == "approve"
    assert task.status == "dispatched"
    assert run.status == "continuation_ready"
    assert run.metadata["approval_decision"]["decision"] == "approve"
    assert schedule_order == ["resume", "inbox"]
    assert sync_calls == []
    assert len(background_coroutines) == 1
    assert RECORDED_EVENTS[-1][0] == "approval_decided"

    asyncio.run(background_coroutines[0])

    assert len(sync_calls) == 1


def test_decide_approval_approve_and_reject_are_atomic_state_transitions():
    service = _load_service()
    task, run, approval = _task_run_approval()
    scheduled = []
    service.schedule_approval_resume = lambda tenant_id, approval_id, operator_id=None: scheduled.append((tenant_id, approval_id, operator_id))

    decided = asyncio.run(service.decide_approval("tenant-1", "approval-1", "read_only", "approver-1", "只读通过"))

    assert decided.status == "approved_read_only"
    assert decided.approver_id == "approver-1"
    assert run.status == "continuation_ready"
    assert run.lease_owner is None
    assert task.status == "dispatched"
    assert RECORDED_EVENTS[-1][0] == "approval_decided"
    assert scheduled == [("tenant-1", "approval-1", "approver-1")]

    task, run, approval = _task_run_approval()
    scheduled.clear()
    decided = asyncio.run(service.decide_approval("tenant-1", "approval-1", "reject", "approver-1", "风险太高"))

    assert decided.status == "rejected"
    assert run.status == "rejected"
    assert run.lease_owner is None
    assert UPDATED_PROGRESS[-1][0]["status"] == "cancelled"
    assert RECORDED_EVENTS[-1][0] == "approval_decided"
    assert scheduled == []


def test_needs_info_keeps_approval_pending_and_actionable():
    service = _load_service()
    task, run, approval = _task_run_approval()

    decided = asyncio.run(service.decide_approval("tenant-1", "approval-1", "needs_info", "approver-1", "补充目标机器"))

    assert decided.status == "pending"
    assert run.status == "waiting_approval"
    assert task.status == "pending_approval"
    assert decided.metadata["needs_info"]["comment"] == "补充目标机器"
    asyncio.run(service.decide_approval("tenant-1", "approval-1", "approve", "approver-2", "补充后通过"))


def test_decide_approval_rejects_terminal_run_without_mutating_records():
    service = _load_service()
    task, run, approval = _task_run_approval()
    run.status = "rejected"

    with pytest.raises(service.CustomError):
        asyncio.run(service.decide_approval("tenant-1", "approval-1", "approve", "approver-1", "late"))

    assert approval.status == "pending"
    assert task.status == "pending_approval"
    assert run.status == "rejected"
    assert SAVED == []
    assert RECORDED_EVENTS == []


def test_resume_approved_approval_reconstructs_execution_from_db_records():
    service = _load_service()
    task, run, approval = _task_run_approval()
    approval.status = "approved_read_only"
    approval.metadata = {"transition": {"policy_mutation": "read_only"}}
    calls = []
    run_manager = types.ModuleType("services.command_center.run_manager")

    async def execute_task_run(task_arg, employee_arg, **kwargs):
        calls.append((task_arg, employee_arg, kwargs))
        return {"content": "done", "run_id": "run-2"}

    run_manager.execute_task_run = execute_task_run
    sys.modules["services.command_center.run_manager"] = run_manager

    result = asyncio.run(service.resume_approved_approval("tenant-1", "approval-1", operator_id="approver-1"))

    assert result == {"content": "done", "run_id": "run-2"}
    assert calls[0][0] is task
    assert calls[0][2]["continuation_approval_id"] == "approval-1"
    assert calls[0][2]["continued_from_run_id"] == "run-1"
    assert calls[0][2]["skip_pre_run_policy"] is True
    assert calls[0][2]["approved_policy_mutation"] == "read_only"
    assert approval.metadata["continuation_run_id"] == "run-2"


def test_resume_records_continuation_run_id_as_soon_as_run_is_checked_out():
    service = _load_service()
    _task, _run, approval = _task_run_approval()
    approval.status = "approved_read_only"
    approval.metadata = {"transition": {"policy_mutation": "read_only"}}
    run_manager = types.ModuleType("services.command_center.run_manager")
    observed = []

    async def execute_task_run(task_arg, employee_arg, **kwargs):
        await kwargs["on_run_checked_out"](SimpleNamespace(run_id="run-2"))
        observed.append(approval.metadata["continuation_run_id"])
        observed.append(approval.metadata["continuation_result_policy"])
        return {"content": "done", "run_id": "run-2"}

    run_manager.execute_task_run = execute_task_run
    sys.modules["services.command_center.run_manager"] = run_manager

    result = asyncio.run(service.resume_approved_approval("tenant-1", "approval-1", operator_id="approver-1"))

    assert result == {"content": "done", "run_id": "run-2"}
    assert observed == ["run-2", "running"]
    assert approval.metadata["continuation_run_id"] == "run-2"
    assert approval.metadata["continuation_result_policy"] == "completed"


def test_resume_active_claim_does_not_start_duplicate_continuation():
    service = _load_service()
    _task, _run, approval = _task_run_approval()
    approval.status = "approved"
    approval.metadata = {
        "continuation_recovery": {
            "status": "running",
            "claimed_at": datetime.now(timezone.utc).isoformat(),
            "operator_id": "approver-1",
        }
    }
    calls = []
    run_manager = types.ModuleType("services.command_center.run_manager")

    async def execute_task_run(task_arg, employee_arg, **kwargs):
        calls.append((task_arg, employee_arg, kwargs))
        return {"content": "unexpected", "run_id": "run-2"}

    run_manager.execute_task_run = execute_task_run
    sys.modules["services.command_center.run_manager"] = run_manager

    result = asyncio.run(service.resume_approved_approval("tenant-1", "approval-1", operator_id="approver-2"))

    assert result["policy"] == "resume_in_progress"
    assert result["approval_id"] == "approval-1"
    assert calls == []


def test_terminal_task_resume_is_not_applicable_and_recovery_scan_is_idempotent():
    service = _load_service()
    task, _run, approval = _task_run_approval()
    approval.status = "approved"
    approval.metadata = {"transition": {"policy_mutation": None}}
    task.status = "completed"
    sys.modules["models.command_center"].CommandApproval.records = [approval]
    execute_calls = []
    run_manager = types.ModuleType("services.command_center.run_manager")

    async def execute_task_run(*args, **kwargs):
        execute_calls.append((args, kwargs))
        return {"content": "unexpected", "run_id": "run-2"}

    run_manager.execute_task_run = execute_task_run
    sys.modules["services.command_center.run_manager"] = run_manager

    first = asyncio.run(service.resume_approved_approval("tenant-1", "approval-1", operator_id="approver-1"))
    save_calls_after_first = list(approval.save_calls)
    second = asyncio.run(service.resume_approved_approval("tenant-1", "approval-1", operator_id="approver-1"))

    resume_scan_calls = []
    original_resume = service.resume_approved_approval

    async def tracked_resume(*args, **kwargs):
        resume_scan_calls.append((args, kwargs))
        return await original_resume(*args, **kwargs)

    service.resume_approved_approval = tracked_resume
    recovered = asyncio.run(service.recover_pending_approval_resumes("tenant-1", operator_id="system-recover"))

    expected = {
        "policy": "resume_not_applicable",
        "approval_id": "approval-1",
        "run_id": None,
        "task_status": "completed",
    }
    assert first == expected
    assert second == expected
    assert approval.metadata["continuation_recovery"]["status"] == "not_applicable"
    assert approval.metadata["continuation_recovery"]["reason"] == "task_terminal"
    assert approval.metadata["continuation_recovery"]["task_status"] == "completed"
    assert "error" not in approval.metadata["continuation_recovery"]
    assert "failed_at" not in approval.metadata["continuation_recovery"]
    assert approval.save_calls == save_calls_after_first
    assert recovered == []
    assert resume_scan_calls == []
    assert execute_calls == []


def test_recover_pending_approval_resumes_schedules_all_eligible_without_awaiting_execution():
    service = _load_service()
    _task, _run, approval = _task_run_approval()
    approval.status = "approved"
    approval.metadata = {"transition": {"policy_mutation": None}}
    second = sys.modules["models.command_center"].CommandApproval(
        id="approval-2",
        tenant_id="tenant-1",
        status="approved_read_only",
        metadata={"transition": {"policy_mutation": "read_only"}},
        save_calls=[],
    )
    active = sys.modules["models.command_center"].CommandApproval(
        id="approval-active",
        tenant_id="tenant-1",
        status="approved",
        metadata={
            "continuation_recovery": {
                "status": "running",
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }
        },
        save_calls=[],
    )
    continued = sys.modules["models.command_center"].CommandApproval(
        id="approval-continued",
        tenant_id="tenant-1",
        status="approved",
        metadata={"continuation_run_id": "run-existing"},
        save_calls=[],
    )
    not_applicable = sys.modules["models.command_center"].CommandApproval(
        id="approval-not-applicable",
        tenant_id="tenant-1",
        status="approved",
        metadata={"continuation_recovery": {"status": "not_applicable"}},
        save_calls=[],
    )
    sys.modules["models.command_center"].CommandApproval.records = [
        approval,
        second,
        active,
        continued,
        not_applicable,
    ]
    scheduled = []
    service.schedule_approval_resume = lambda tenant_id, approval_id, operator_id=None: scheduled.append(
        (tenant_id, approval_id, operator_id)
    )

    async def fail_if_execution_is_awaited(*args, **kwargs):
        raise AssertionError("scan must not await approval execution")

    service.resume_approved_approval = fail_if_execution_is_awaited

    recovered = asyncio.run(service.recover_pending_approval_resumes("tenant-1", operator_id="system-recover"))

    assert recovered == [
        {"policy": "resume_scheduled", "approval_id": "approval-1"},
        {"policy": "resume_scheduled", "approval_id": "approval-2"},
    ]
    assert scheduled == [
        ("tenant-1", "approval-1", "system-recover"),
        ("tenant-1", "approval-2", "system-recover"),
    ]


def test_successful_resume_with_run_id_is_not_recovered_twice():
    service = _load_service()
    task, _run, approval = _task_run_approval()
    approval.status = "approved"
    approval.metadata = {"transition": {"policy_mutation": None}}
    task.run_id = "run-2"
    sys.modules["models.command_center"].CommandApproval.records = [approval]

    calls = []
    run_manager = types.ModuleType("services.command_center.run_manager")

    async def execute_task_run(task_arg, employee_arg, **kwargs):
        calls.append((task_arg, employee_arg, kwargs))
        return {"content": "done", "run_id": task_arg.run_id}

    run_manager.execute_task_run = execute_task_run
    sys.modules["services.command_center.run_manager"] = run_manager

    result = asyncio.run(service.resume_approved_approval("tenant-1", "approval-1", operator_id="approver-1"))
    recovered = asyncio.run(service.recover_pending_approval_resumes("tenant-1", operator_id="system-recover"))

    assert result == {"content": "done", "run_id": "run-2"}
    assert recovered == []
    assert len(calls) == 1
    assert approval.metadata["continuation_run_id"] == "run-2"


def test_resume_uses_existing_continuation_run_after_metadata_crash_window():
    service = _load_service()
    _task, _run, approval = _task_run_approval()
    approval.status = "approved"
    approval.metadata = {"transition": {"policy_mutation": None}}
    existing_run = sys.modules["models.command_center"].CommandTaskRun(
        id="run-row-2",
        tenant_id="tenant-1",
        task_id="task-1",
        run_id="run-2",
        status="completed",
        metadata={"continuation_approval_id": "approval-1"},
    )
    sys.modules["models.command_center"].CommandTaskRun.records = [existing_run]

    calls = []
    run_manager = types.ModuleType("services.command_center.run_manager")

    async def execute_task_run(task_arg, employee_arg, **kwargs):
        calls.append((task_arg, employee_arg, kwargs))
        return {"content": "unexpected", "run_id": "run-3"}

    run_manager.execute_task_run = execute_task_run
    sys.modules["services.command_center.run_manager"] = run_manager

    result = asyncio.run(service.resume_approved_approval("tenant-1", "approval-1", operator_id="system-recover"))

    assert result == {"policy": "already_continued", "run_id": "run-2", "approval_id": "approval-1"}
    assert calls == []
    assert approval.metadata["continuation_run_id"] == "run-2"
