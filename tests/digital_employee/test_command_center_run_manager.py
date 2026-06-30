# coding=utf-8
import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types


CREATED_RUNS = []
RECORDED_EVENTS = []
UPDATED_PROGRESS = []
FINALIZED_SAVES = []


def _install_stubs():
    CREATED_RUNS.clear()
    RECORDED_EVENTS.clear()
    UPDATED_PROGRESS.clear()
    FINALIZED_SAVES.clear()
    for name in [
        "models",
        "models.command_center",
        "settings",
        "services",
        "services.command_center",
        "services.command_center.manager",
        "services.command_center.approval_contract",
        "services.command_center.approval_service",
        "services.digital_employee",
        "services.digital_employee.prompt_builder",
        "services.digital_employee.execute_coordinator",
    ]:
        sys.modules.pop(name, None)

    models_module = types.ModuleType("models")
    sys.modules["models"] = models_module

    command_center = types.ModuleType("models.command_center")
    command_center.COMMAND_APPROVAL_STATUS_PENDING = "pending"
    command_center.COMMAND_RUN_STATUS_CANCELLED = "cancelled"
    command_center.COMMAND_RUN_STATUS_CHECKED_OUT = "checked_out"
    command_center.COMMAND_RUN_STATUS_COMPLETED = "completed"
    command_center.COMMAND_RUN_STATUS_FAILED = "failed"
    command_center.COMMAND_RUN_STATUS_HANDED_OFF = "handed_off"
    command_center.COMMAND_RUN_STATUS_RUNNING = "running"
    command_center.COMMAND_RUN_STATUS_STALE = "stale"
    command_center.COMMAND_RUN_STATUS_WAITING_APPROVAL = "waiting_approval"
    command_center.COMMAND_RUNTIME_LION_AGENT = "lion_agent"
    command_center.COMMAND_TASK_STATUS_CANCELLED = "cancelled"
    command_center.COMMAND_TASK_STATUS_COMPLETED = "completed"
    command_center.COMMAND_TASK_STATUS_DISPATCHED = "dispatched"
    command_center.COMMAND_TASK_STATUS_FAILED = "failed"
    command_center.COMMAND_TASK_STATUS_HANDED_OFF = "handed_off"
    command_center.COMMAND_TASK_STATUS_PENDING_APPROVAL = "pending_approval"

    class CommandAgent(SimpleNamespace):
        pass

    class CommandTask(SimpleNamespace):
        async def save(self, update_fields=None):
            self.saved_update_fields.append(update_fields)

    class CommandTaskRun(SimpleNamespace):
        @classmethod
        async def create(cls, **kwargs):
            run = cls(**kwargs)
            run.saved_update_fields = []
            if "id" not in kwargs:
                run.id = f"row-{len(CREATED_RUNS) + 1}"
            CREATED_RUNS.append(run)
            return run

        async def save(self, update_fields=None):
            self.saved_update_fields.append(update_fields)
            FINALIZED_SAVES.append((self.status, update_fields))

    class CommandApproval(SimpleNamespace):
        created = []

        @classmethod
        async def create(cls, **kwargs):
            approval = cls(**kwargs)
            cls.created.append(approval)
            return approval

    command_center.CommandAgent = CommandAgent
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

    async def record_task_event(task, event_type, message=None, operator_id=None, metadata=None):
        RECORDED_EVENTS.append((event_type, message, operator_id, metadata or {}))
        return SimpleNamespace(event_type=event_type, metadata=metadata or {})

    async def update_task_progress(task, data, operator_id=None):
        UPDATED_PROGRESS.append((data, operator_id))
        if "status" in data:
            task.status = data["status"]
        return task

    manager.record_task_event = record_task_event
    manager.update_task_progress = update_task_progress
    sys.modules["services.command_center.manager"] = manager

    approval_contract = types.ModuleType("services.command_center.approval_contract")
    approval_contract.CONTINUATION_MODE = "lion_mgmt_held_continuation"
    sys.modules["services.command_center.approval_contract"] = approval_contract
    approval_service = types.ModuleType("services.command_center.approval_service")

    async def create_approval_request(task, run, **kwargs):
        employee_ref = run.employee
        if hasattr(employee_ref, "__await__"):
            employee_ref = await employee_ref
        return await CommandApproval.create(
            tenant_id=task.tenant_id,
            task=task,
            run=run,
            employee=employee_ref,
            status="pending",
            trigger_type=kwargs.get("trigger_type"),
            risk_level=kwargs.get("risk_level", "medium"),
            policy_snapshot=run.policy_snapshot,
            requested_action=kwargs.get("requested_action") or {},
            metadata={"source": "stub"},
        )

    async def enforce_pre_run_policy(task, run, operator_id=None):
        return {"decision": "allow"}

    approval_service.create_approval_request = create_approval_request
    approval_service.enforce_pre_run_policy = enforce_pre_run_policy
    sys.modules["services.command_center.approval_service"] = approval_service

    digital_employee_pkg = types.ModuleType("services.digital_employee")
    digital_employee_pkg.__path__ = []
    sys.modules["services.digital_employee"] = digital_employee_pkg

    prompt_builder = types.ModuleType("services.digital_employee.prompt_builder")
    prompt_builder.build_workspace_payload = lambda employee, role, skills: {
        "manifest": {"employee": {"id": employee.id}, "workspace_etag": "etag-1"},
        "policy": {"approval": {"default": "allow", "skills": {"write_file": "allow"}}, "filesystem": {"write": "allow"}},
    }
    prompt_builder.build_workspace_snapshots = lambda employee, role, skills: {
        "workspace_etag": "etag-1",
        "manifest_snapshot": {"employee": {"id": employee.id}, "workspace_etag": "etag-1"},
        "policy_snapshot": {"approval": {"default": "allow"}},
    }
    prompt_builder.render_workspace_files = lambda employee, role, skills: {
        "system_prompt.md": "role",
        "manifest.json": {"employee": {"id": employee.id}, "workspace_etag": "etag-1"},
        "policy.json": {"approval": {"default": "allow", "skills": {"write_file": "allow"}}, "filesystem": {"write": "allow"}},
    }
    prompt_builder.workspace_etag = lambda role, skills, employee: "etag-1"
    sys.modules["services.digital_employee.prompt_builder"] = prompt_builder


def _load_run_manager():
    _install_stubs()
    path = Path(__file__).resolve().parents[2] / "services/command_center/run_manager.py"
    spec = importlib.util.spec_from_file_location("services.command_center.run_manager", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["services.command_center.run_manager"] = module
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


def _task():
    return sys.modules["models.command_center"].CommandTask(
        id="task-1",
        tenant_id="tenant-1",
        status="dispatched",
        progress=0,
        run_id=None,
        saved_update_fields=[],
    )


def _employee():
    employee = sys.modules["models.command_center"].CommandAgent(
        id="employee-1",
        tenant_id="tenant-1",
        role_key="ops",
        skill_keys=[],
    )
    employee.role = _async_value(None)
    return employee


def test_checkout_run_records_lease_snapshot_and_task_pointer():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()

    run = asyncio.run(run_manager.checkout_run(task, employee, operator_id="operator-1", lease_owner="worker-1"))

    assert run.status == "checked_out"
    assert run.runtime == "lion_agent"
    assert run.lease_owner == "worker-1"
    assert run.workspace_etag == "etag-1"
    assert run.manifest_snapshot["employee"]["id"] == "employee-1"
    assert run.policy_snapshot["approval"]["default"] == "allow"
    assert task.run_id == run.run_id
    assert task.saved_update_fields == [["run_id", "utime"]]
    assert RECORDED_EVENTS[0][0] == "run_checked_out"


def test_runtime_event_normalization_updates_run_and_approval_wait_state():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    run.employee = _async_value(employee)

    normalized = asyncio.run(
        run_manager.record_runtime_event(
            task,
            run,
            "approval_required",
            {"tool": "write_file", "risk_level": "high"},
            operator_id="operator-1",
        )
    )

    assert normalized["event_type"] == "approval_required"
    assert run.status == "waiting_approval"
    assert task.status == "pending_approval"
    assert RECORDED_EVENTS[-1][0] == "approval_required"
    approval = sys.modules["models.command_center"].CommandApproval.created[-1]
    assert approval.status == "pending"
    assert approval.policy_snapshot == {"approval": {"default": "allow"}}
    assert approval.requested_action == {"tool": "write_file", "risk_level": "high"}


def test_runtime_approval_uses_agentscope_requested_action_shape():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    run.employee = _async_value(employee)

    normalized = asyncio.run(
        run_manager.record_runtime_event(
            task,
            run,
            "approval_required",
            {
                "data": {
                    "run_id": "run-1",
                    "session_id": "session-1",
                    "requested_action": {"tool": "write_file", "risk_level": "high"},
                }
            },
            operator_id="operator-1",
        )
    )

    approval = sys.modules["models.command_center"].CommandApproval.created[-1]
    assert normalized["requested_action"] == {"tool": "write_file", "risk_level": "high"}
    assert normalized["metadata"]["payload"]["data"]["session_id"] == "session-1"
    assert approval.requested_action == {"tool": "write_file", "risk_level": "high"}
    assert approval.risk_level == "high"


def test_finalize_and_stale_recovery_mark_terminal_run():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    run.task = _async_value(task)
    run.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)

    class Query:
        def filter(self, **kwargs):
            return self

        async def all(self):
            return [run]

    sys.modules["models.command_center"].CommandTaskRun.filter = classmethod(lambda cls, **kwargs: Query())

    recovered = asyncio.run(run_manager.recover_stale_runs(now=datetime.now(timezone.utc)))

    assert recovered == [run]
    assert run.status == "stale"
    assert run.error["message"] == "run lease expired"
    assert UPDATED_PROGRESS[-1][0]["status"] == "failed"
    assert RECORDED_EVENTS[-1][0] == "run_stale_recovered"


def test_execute_task_run_stops_when_runtime_requests_approval():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    execute_coordinator = types.ModuleType("services.digital_employee.execute_coordinator")

    async def run(task_arg, employee_arg, **kwargs):
        control = await kwargs["on_runtime_event"]("approval_required", {"tool": "write_file", "risk_level": "high"})
        return {"content": "", "policy": control["event_type"], "run_id": kwargs["command_run"].run_id, "runtime_event": control}

    execute_coordinator.run = run
    sys.modules["services.digital_employee.execute_coordinator"] = execute_coordinator

    result = asyncio.run(run_manager.execute_task_run(task, employee, operator_id="operator-1"))

    assert result["policy"] == "approval_required"
    assert CREATED_RUNS[-1].status == "waiting_approval"
    assert task.status == "pending_approval"
    assert all(save[0] != "completed" for save in FINALIZED_SAVES)


def test_execute_task_run_records_real_continuation_start_on_new_run():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    execute_coordinator = types.ModuleType("services.digital_employee.execute_coordinator")

    async def run(task_arg, employee_arg, **kwargs):
        return {"content": "done"}

    execute_coordinator.run = run
    sys.modules["services.digital_employee.execute_coordinator"] = execute_coordinator

    result = asyncio.run(
        run_manager.execute_task_run(
            task,
            employee,
            operator_id="approver-1",
            continuation_approval_id="approval-1",
            continued_from_run_id="run-1",
            skip_pre_run_policy=True,
            approved_policy_mutation="read_only",
        )
    )

    assert result["content"] == "done"
    assert result["run_id"] == CREATED_RUNS[-1].run_id
    assert CREATED_RUNS[-1].metadata["continuation_approval_id"] == "approval-1"
    assert CREATED_RUNS[-1].metadata["workspace_id"].startswith("employee-1:approval-")
    assert CREATED_RUNS[-1].metadata["workspace_id"].endswith(":read-only")
    assert CREATED_RUNS[-1].metadata["skip_pre_run_policy"] is True
    assert CREATED_RUNS[-1].workspace_etag.startswith("read-only-")
    assert len(CREATED_RUNS[-1].workspace_etag) < 64
    assert CREATED_RUNS[-1].policy_snapshot["continuation_mutation"]["mode"] == "read_only"
    assert CREATED_RUNS[-1].policy_snapshot["filesystem"]["write"] == "deny"
    assert CREATED_RUNS[-1].policy_snapshot["approval"]["default"] == "approval_required"
    assert any(event[0] == "continuation_started" and event[3]["continued_from_run_id"] == "run-1" for event in RECORDED_EVENTS)
