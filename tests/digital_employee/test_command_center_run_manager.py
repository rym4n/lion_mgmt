# coding=utf-8
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import types

import pytest


CREATED_RUNS = []
CREATED_TASKS = []
CREATED_AGENTS = []
RECORDED_EVENTS = []
UPDATED_PROGRESS = []
FINALIZED_SAVES = []
PERSISTED_RUN_METADATA = {}
RUNTIME_EVENT_BUFFER = {}
RUNTIME_EVENT_BULK_CREATES = []
PERSISTED_RUNTIME_EVENTS = {}
STUB_MODULES = [
    "models",
    "models.command_center",
    "settings",
    "services",
    "services.command_center",
    "services.command_center.manager",
    "services.command_center.approval_contract",
    "services.command_center.approval_service",
    "services.command_center.run_manager",
    "services.command_center.realtime",
    "services.digital_employee",
    "services.digital_employee.prompt_builder",
    "services.digital_employee.execute_coordinator",
    "tortoise",
    "tortoise.transactions",
    "aiocache",
]
_MISSING_MODULE = object()


@pytest.fixture(autouse=True)
def _restore_stubbed_modules():
    original_modules = {name: sys.modules.get(name, _MISSING_MODULE) for name in STUB_MODULES}
    yield
    for name, original in original_modules.items():
        if original is _MISSING_MODULE:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


def _install_stubs():
    CREATED_RUNS.clear()
    CREATED_TASKS.clear()
    CREATED_AGENTS.clear()
    RECORDED_EVENTS.clear()
    UPDATED_PROGRESS.clear()
    FINALIZED_SAVES.clear()
    PERSISTED_RUN_METADATA.clear()
    RUNTIME_EVENT_BUFFER.clear()
    RUNTIME_EVENT_BULK_CREATES.clear()
    PERSISTED_RUNTIME_EVENTS.clear()
    for name in STUB_MODULES:
        sys.modules.pop(name, None)

    models_module = types.ModuleType("models")
    sys.modules["models"] = models_module

    command_center = types.ModuleType("models.command_center")
    command_center.COMMAND_APPROVAL_STATUS_PENDING = "pending"
    command_center.COMMAND_RUN_STATUS_CANCELLED = "cancelled"
    command_center.COMMAND_RUN_STATUS_CHECKED_OUT = "checked_out"
    command_center.COMMAND_RUN_STATUS_COMPLETED = "completed"
    command_center.COMMAND_RUN_STATUS_CONTINUATION_READY = "continuation_ready"
    command_center.COMMAND_RUN_STATUS_FAILED = "failed"
    command_center.COMMAND_RUN_STATUS_HANDED_OFF = "handed_off"
    command_center.COMMAND_RUN_STATUS_REJECTED = "rejected"
    command_center.COMMAND_RUN_STATUS_RUNNING = "running"
    command_center.COMMAND_RUN_STATUS_STALE = "stale"
    command_center.COMMAND_RUN_STATUS_WAITING_APPROVAL = "waiting_approval"
    command_center.COMMAND_RUN_TERMINAL_STATUSES = {
        "completed",
        "failed",
        "cancelled",
        "rejected",
        "stale",
        "handed_off",
    }
    command_center.COMMAND_RUNTIME_LION_AGENT = "lion_agent"
    command_center.COMMAND_TASK_STATUS_CANCELLED = "cancelled"
    command_center.COMMAND_TASK_STATUS_COMPLETED = "completed"
    command_center.COMMAND_TASK_STATUS_DISPATCHED = "dispatched"
    command_center.COMMAND_TASK_STATUS_FAILED = "failed"
    command_center.COMMAND_TASK_STATUS_HANDED_OFF = "handed_off"
    command_center.COMMAND_TASK_STATUS_PENDING_APPROVAL = "pending_approval"
    command_center.COMMAND_TASK_STATUS_RUNNING = "running"
    command_center.COMMAND_TASK_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "handed_off"}

    class CommandAgent(SimpleNamespace):
        class Query:
            def __init__(self, filters):
                self.filters = filters

            def using_db(self, connection):
                return self

            def select_for_update(self, **kwargs):
                return self

            async def first(self):
                return next(
                    (
                        agent for agent in CREATED_AGENTS
                        if all(getattr(agent, key, None) == value for key, value in self.filters.items())
                    ),
                    None,
                )

        @classmethod
        def filter(cls, **kwargs):
            return cls.Query(kwargs)

        async def save(self, using_db=None, update_fields=None):
            self.saved_update_fields.append(update_fields)

    class CommandTask(SimpleNamespace):
        class Query:
            def __init__(self, filters):
                self.filters = filters

            def using_db(self, connection):
                return self

            def select_for_update(self, **kwargs):
                return self

            def filter(self, **kwargs):
                self.filters.update(kwargs)
                return self

            def _matches(self, task):
                for key, value in self.filters.items():
                    if key.endswith("__lt"):
                        current = getattr(task, key[:-4], None)
                        matches = current is not None and current < value
                    else:
                        current = getattr(task, key, False) if key == "deleted" else getattr(task, key, None)
                        matches = current == value
                    if not matches:
                        return False
                return True

            async def first(self):
                return next((task for task in CREATED_TASKS if self._matches(task)), None)

            async def all(self):
                return [task for task in CREATED_TASKS if self._matches(task)]

        @classmethod
        def filter(cls, **kwargs):
            return cls.Query(kwargs)

        async def save(self, using_db=None, update_fields=None):
            self.saved_update_fields.append(update_fields)

    class CommandTaskRun(SimpleNamespace):
        class Query:
            def __init__(self, filters):
                self.filters = filters

            def select_for_update(self, **kwargs):
                return self

            def using_db(self, connection):
                return self

            def filter(self, **kwargs):
                self.filters.update(kwargs)
                return self

            def _matches(self, run):
                for key, value in self.filters.items():
                    if key.endswith("__in"):
                        matches = getattr(run, key[:-4], None) in value
                    elif key.endswith("__lt"):
                        current = getattr(run, key[:-4], None)
                        matches = current is not None and current < value
                    else:
                        current = getattr(run, key, False) if key == "deleted" else getattr(run, key, None)
                        matches = current == value
                    if not matches:
                        return False
                return True

            async def first(self):
                return next((run for run in CREATED_RUNS if self._matches(run)), None)

            async def all(self):
                return [run for run in CREATED_RUNS if self._matches(run)]

        @classmethod
        def filter(cls, **kwargs):
            return cls.Query(kwargs)

        @classmethod
        async def create(cls, **kwargs):
            defaults = {
                "session_id": None,
                "tool_calls": [],
                "resource_accesses": [],
                "usage": {},
                "cost": {},
                "transcript_ref": None,
                "evidence_refs": [],
                "error": {},
                "finished_at": None,
                "deleted": False,
            }
            run = cls(**{**defaults, **kwargs})
            run.task_id = getattr(kwargs.get("task"), "id", kwargs.get("task_id"))
            run.saved_update_fields = []
            if "id" not in kwargs:
                run.id = f"row-{len(CREATED_RUNS) + 1}"
            run.refreshed_fields = []
            PERSISTED_RUN_METADATA[run.id] = deepcopy(run.metadata)
            CREATED_RUNS.append(run)
            return run

        async def refresh_from_db(self, fields=None, using_db=None):
            self.refreshed_fields.append(fields)
            if fields is None or "metadata" in fields:
                self.metadata = deepcopy(PERSISTED_RUN_METADATA[self.id])

        async def save(self, using_db=None, update_fields=None):
            self.saved_update_fields.append(update_fields)
            if update_fields is None or "metadata" in update_fields:
                PERSISTED_RUN_METADATA[self.id] = deepcopy(self.metadata)
            FINALIZED_SAVES.append((self.status, update_fields))

    class CommandApproval(SimpleNamespace):
        created = []
        records = []

        class Query:
            def __init__(self, filters):
                self.filters = filters

            def using_db(self, connection):
                return self

            def select_for_update(self, **kwargs):
                return self

            def order_by(self, *args):
                return self

            def _matches(self, approval):
                for key, value in self.filters.items():
                    if key.endswith("__in"):
                        matches = getattr(approval, key[:-4], None) in value
                    else:
                        current = getattr(approval, key, False) if key == "deleted" else getattr(approval, key, None)
                        matches = current == value
                    if not matches:
                        return False
                return True

            async def first(self):
                return next((approval for approval in CommandApproval.records if self._matches(approval)), None)

            async def all(self):
                return [approval for approval in CommandApproval.records if self._matches(approval)]

        @classmethod
        def filter(cls, **kwargs):
            return cls.Query(kwargs)

        @classmethod
        async def create(cls, **kwargs):
            approval = cls(**kwargs)
            cls.created.append(approval)
            return approval

    class CommandTaskEvent(SimpleNamespace):
        @classmethod
        async def bulk_create(cls, events, ignore_conflicts=False, **_kwargs):
            inserted = []
            for event in events:
                if ignore_conflicts and event.id in PERSISTED_RUNTIME_EVENTS:
                    continue
                PERSISTED_RUNTIME_EVENTS[event.id] = event
                inserted.append(event)
            RUNTIME_EVENT_BULK_CREATES.append(inserted)
            return inserted

    command_center.CommandAgent = CommandAgent
    command_center.CommandTask = CommandTask
    command_center.CommandTaskRun = CommandTaskRun
    command_center.CommandApproval = CommandApproval
    command_center.CommandTaskEvent = CommandTaskEvent
    sys.modules["models.command_center"] = command_center

    tortoise = types.ModuleType("tortoise")
    tortoise.__path__ = []
    sys.modules["tortoise"] = tortoise
    transactions = types.ModuleType("tortoise.transactions")

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    transactions.in_transaction = Transaction
    sys.modules["tortoise.transactions"] = transactions

    class RedisClient:
        async def rpush(self, key, value):
            RUNTIME_EVENT_BUFFER.setdefault(key, []).append(value)
            return len(RUNTIME_EVENT_BUFFER[key])

        async def expire(self, _key, _ttl):
            return True

        async def lrange(self, key, start, end):
            values = RUNTIME_EVENT_BUFFER.get(key, [])
            return values[start:] if end == -1 else values[start : end + 1]

        async def delete(self, key):
            RUNTIME_EVENT_BUFFER.pop(key, None)
            return 1

    aiocache = types.ModuleType("aiocache")
    aiocache.caches = SimpleNamespace(get=lambda _name: SimpleNamespace(client=RedisClient()))
    sys.modules["aiocache"] = aiocache

    settings = types.ModuleType("settings")
    settings.COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS = 300
    settings.TZ = timezone.utc
    sys.modules["settings"] = settings

    services = types.ModuleType("services")
    services.__path__ = []
    sys.modules["services"] = services
    command_center_pkg = types.ModuleType("services.command_center")
    command_center_pkg.__path__ = []
    sys.modules["services.command_center"] = command_center_pkg
    realtime = types.ModuleType("services.command_center.realtime")

    async def publish_command_center_update(*_args, **_kwargs):
        return None

    realtime.publish_command_center_update = publish_command_center_update
    sys.modules["services.command_center.realtime"] = realtime
    manager = types.ModuleType("services.command_center.manager")

    async def record_task_event(task, event_type, message=None, operator_id=None, metadata=None, using_db=None):
        RECORDED_EVENTS.append((event_type, message, operator_id, metadata or {}))
        return SimpleNamespace(event_type=event_type, metadata=metadata or {})

    async def apply_task_progress_in_transaction(
        connection,
        task,
        data,
        operator_id=None,
        *,
        locked_task=None,
        expected_run_id=None,
    ):
        locked_task = locked_task or next((item for item in CREATED_TASKS if item.id == task.id), None)
        if locked_task is None or (expected_run_id is not None and locked_task.run_id != expected_run_id):
            return None
        old_status = locked_task.status
        if old_status not in command_center.COMMAND_TASK_TERMINAL_STATUSES:
            for field in ("progress", "status", "run_id", "result"):
                if field in data:
                    setattr(locked_task, field, data[field])
        UPDATED_PROGRESS.append((data, operator_id))
        entered_terminal = (
            old_status not in command_center.COMMAND_TASK_TERMINAL_STATUSES
            and locked_task.status in command_center.COMMAND_TASK_TERMINAL_STATUSES
        )
        terminal_agent = getattr(locked_task, "assigned_agent", None) if entered_terminal else None
        if terminal_agent:
            terminal_agent.load = max(0, terminal_agent.load - 1)
            if terminal_agent.current_task_id == locked_task.id:
                terminal_agent.current_task_id = None
            if terminal_agent.status == "busy" and terminal_agent.load < terminal_agent.max_concurrent:
                terminal_agent.status = "idle"
        return locked_task, terminal_agent, entered_terminal

    async def complete_task_progress_after_commit(task, transition, data):
        locked_task = transition[0]
        if locked_task is not task:
            for field in ("progress", "status", "run_id", "result"):
                if hasattr(locked_task, field):
                    setattr(task, field, getattr(locked_task, field))
        return locked_task

    async def update_task_progress(task, data, operator_id=None):
        transition = await apply_task_progress_in_transaction(None, task, data, operator_id)
        if transition is None:
            return task
        await complete_task_progress_after_commit(task, transition, data)
        return task

    manager.record_task_event = record_task_event
    manager.apply_task_progress_in_transaction = apply_task_progress_in_transaction
    manager.complete_task_progress_after_commit = complete_task_progress_after_commit
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

    def approval_resume_claim_is_active(approval, now=None):
        if approval.status not in {"approved", "approved_read_only"}:
            return False
        metadata = approval.metadata if isinstance(approval.metadata, dict) else {}
        if metadata.get("continuation_run_id"):
            return False
        recovery = metadata.get("continuation_recovery")
        if not isinstance(recovery, dict) or recovery.get("status") != "running":
            return False
        try:
            claimed_at = datetime.fromisoformat(str(recovery.get("claimed_at")))
        except (TypeError, ValueError):
            return False
        return (now or datetime.now(timezone.utc)) - claimed_at < timedelta(minutes=5)

    approval_service.create_approval_request = create_approval_request
    approval_service.enforce_pre_run_policy = enforce_pre_run_policy
    approval_service.approval_resume_claim_is_active = approval_resume_claim_is_active
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
    task = sys.modules["models.command_center"].CommandTask(
        id="task-1",
        tenant_id="tenant-1",
        deleted=False,
        status="dispatched",
        progress=0,
        timeout_seconds=3600,
        run_id=None,
        started_at=None,
        finished_at=None,
        result=None,
        saved_update_fields=[],
    )
    CREATED_TASKS.append(task)
    return task


def _employee():
    employee = sys.modules["models.command_center"].CommandAgent(
        id="employee-1",
        tenant_id="tenant-1",
        role_key="ops",
        skill_keys=[],
        agent_key="ops",
        runtime="lion_agent",
        deleted=False,
        enabled=True,
        status="idle",
        load=0,
        max_concurrent=1,
        current_task_id=None,
        saved_update_fields=[],
    )
    employee.role = _async_value(None)
    CREATED_AGENTS.append(employee)
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
    assert task.status == "running"
    assert task.started_at is not None
    assert task.saved_update_fields == [["run_id", "status", "started_at", "utime"]]
    assert RECORDED_EVENTS[0][0] == "run_checked_out"


def test_checkout_run_rejects_second_checkout_for_same_task():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    first_run = asyncio.run(run_manager.checkout_run(task, employee))
    initial_events = list(RECORDED_EVENTS)
    initial_saves = list(task.saved_update_fields)

    with pytest.raises(ValueError, match="task is not dispatchable"):
        asyncio.run(run_manager.checkout_run(task, employee))

    assert CREATED_RUNS == [first_run]
    assert task.status == "running"
    assert task.run_id == first_run.run_id
    assert task.saved_update_fields == initial_saves
    assert RECORDED_EVENTS == initial_events


def test_stream_runtime_event_is_buffered_without_postgres_event_insert():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))

    normalized = asyncio.run(
        run_manager.record_runtime_event(task, run, "delta", {"content": "hello"})
    )

    assert normalized["event_type"] == "runtime_delta"
    assert [event for event in RECORDED_EVENTS if event[0] == "runtime_delta"] == []
    key = run_manager.runtime_event_buffer_key(task, run)
    assert len(RUNTIME_EVENT_BUFFER[key]) == 1
    assert json.loads(RUNTIME_EVENT_BUFFER[key][0])["event"]["event_type"] == "runtime_delta"


def test_terminal_finalize_bulk_persists_buffered_runtime_events_and_clears_redis():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))
    asyncio.run(run_manager.record_runtime_event(task, run, "thinking", {"content": "working"}))
    asyncio.run(run_manager.record_runtime_event(task, run, "delta", {"content": "done"}))

    asyncio.run(
        run_manager.finalize_run_and_task(
            run,
            task,
            "completed",
            {"status": "completed", "message": "完成"},
        )
    )

    assert len(RUNTIME_EVENT_BULK_CREATES) == 1
    assert [event.event_type for event in RUNTIME_EVENT_BULK_CREATES[0]] == [
        "runtime_thinking",
        "runtime_delta",
    ]
    assert run_manager.runtime_event_buffer_key(task, run) not in RUNTIME_EVENT_BUFFER


def test_runtime_event_falls_back_to_one_database_event_when_redis_is_unavailable():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))

    class FailingRedis:
        async def rpush(self, *_args):
            raise RuntimeError("redis unavailable")

        async def lrange(self, *_args):
            raise RuntimeError("redis unavailable")

    sys.modules["aiocache"].caches = SimpleNamespace(
        get=lambda _name: SimpleNamespace(client=FailingRedis())
    )

    normalized = asyncio.run(run_manager.record_runtime_event(task, run, "progress", {"message": "fallback"}))

    assert normalized["event_type"] == "runtime_progress"
    assert next(iter(PERSISTED_RUNTIME_EVENTS.values())).event_type == "runtime_progress"


def test_ambiguous_redis_append_and_database_fallback_share_one_event_id():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))

    class AppendThenTimeout:
        async def rpush(self, key, value):
            RUNTIME_EVENT_BUFFER.setdefault(key, []).append(value)
            raise RuntimeError("response lost after append")

        async def expire(self, *_args):
            return True

        async def lrange(self, key, start, end):
            values = RUNTIME_EVENT_BUFFER.get(key, [])
            return values[start:] if end == -1 else values[start : end + 1]

        async def delete(self, key):
            RUNTIME_EVENT_BUFFER.pop(key, None)
            return 1

    redis_client = AppendThenTimeout()
    sys.modules["aiocache"].caches = SimpleNamespace(
        get=lambda _name: SimpleNamespace(client=redis_client)
    )

    asyncio.run(run_manager.record_runtime_event(task, run, "delta", {"content": "hello"}))
    event_id = next(iter(PERSISTED_RUNTIME_EVENTS))
    envelope = json.loads(RUNTIME_EVENT_BUFFER[run_manager.runtime_event_buffer_key(task, run)][0])

    asyncio.run(run_manager.finalize_run_and_task(run, task, "completed", {"status": "completed"}))

    assert envelope["event_id"] == event_id
    assert len(PERSISTED_RUNTIME_EVENTS) == 1
    assert next(iter(PERSISTED_RUNTIME_EVENTS.values())).event_type == "runtime_delta"


def test_runtime_event_expire_failure_does_not_duplicate_successful_append():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))

    class ExpireFails:
        async def rpush(self, key, value):
            RUNTIME_EVENT_BUFFER.setdefault(key, []).append(value)
            return 1

        async def expire(self, *_args):
            raise RuntimeError("expire unavailable")

    sys.modules["aiocache"].caches = SimpleNamespace(
        get=lambda _name: SimpleNamespace(client=ExpireFails())
    )

    asyncio.run(run_manager.record_runtime_event(task, run, "delta", {"content": "hello"}))

    assert len(RUNTIME_EVENT_BUFFER[run_manager.runtime_event_buffer_key(task, run)]) == 1
    assert [event for event in RECORDED_EVENTS if event[0] == "runtime_delta"] == []


def test_terminal_finalize_does_not_commit_when_redis_buffer_read_fails():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))
    asyncio.run(run_manager.record_runtime_event(task, run, "delta", {"content": "hello"}))

    class ReadFails:
        async def lrange(self, *_args):
            raise RuntimeError("redis unavailable")

    sys.modules["aiocache"].caches = SimpleNamespace(
        get=lambda _name: SimpleNamespace(client=ReadFails())
    )

    with pytest.raises(RuntimeError, match="redis unavailable"):
        asyncio.run(run_manager.finalize_run_and_task(run, task, "completed", {"status": "completed"}))

    assert RUNTIME_EVENT_BULK_CREATES == []
    assert len(RUNTIME_EVENT_BUFFER[run_manager.runtime_event_buffer_key(task, run)]) == 1


def test_approval_required_flushes_buffer_at_state_checkpoint():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))
    asyncio.run(run_manager.record_runtime_event(task, run, "delta", {"content": "hello"}))

    asyncio.run(run_manager.record_runtime_event(task, run, "approval_required", {"tool": "write_file"}))

    assert len(RUNTIME_EVENT_BULK_CREATES) == 1
    assert [event.event_type for event in RUNTIME_EVENT_BULK_CREATES[0]] == [
        "runtime_delta",
        "approval_required",
    ]
    assert run_manager.runtime_event_buffer_key(task, run) not in RUNTIME_EVENT_BUFFER


def test_terminal_finalize_does_not_repeat_database_batch_when_redis_cleanup_fails():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))
    asyncio.run(run_manager.record_runtime_event(task, run, "delta", {"content": "hello"}))

    redis_client = sys.modules["aiocache"].caches.get("redis_mgmt").client

    async def failed_delete(_key):
        raise RuntimeError("delete unavailable")

    redis_client.delete = failed_delete
    sys.modules["aiocache"].caches = SimpleNamespace(
        get=lambda _name: SimpleNamespace(client=redis_client)
    )

    asyncio.run(run_manager.finalize_run_and_task(run, task, "cancelled", {"status": "cancelled"}))
    asyncio.run(run_manager.finalize_run_and_task(run, task, "cancelled", {"status": "cancelled"}))

    assert sum(len(batch) for batch in RUNTIME_EVENT_BULK_CREATES) == 1


def test_runtime_event_normalization_updates_run_and_approval_wait_state():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    run.employee = _async_value(employee)
    initial_lease_expires_at = run.lease_expires_at

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
    assert run.lease_expires_at > initial_lease_expires_at
    assert task.status == "pending_approval"
    assert [event.event_type for event in RUNTIME_EVENT_BULK_CREATES[0]] == ["approval_required"]
    approval = sys.modules["models.command_center"].CommandApproval.created[-1]
    assert approval.status == "pending"
    assert approval.policy_snapshot == {"approval": {"default": "allow"}}
    assert approval.requested_action == {"tool": "write_file", "risk_level": "high"}


def test_runtime_progress_omits_binary_output_and_bounds_persisted_event():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))
    payload = {
        "message": "Read result received",
        "type": "TOOL_RESULT_TEXT_DELTA",
        "data": {"tool": "Read", "output": "\x7fELF\x00" + "x" * 200_000},
    }

    normalized = asyncio.run(run_manager.record_runtime_event(task, run, "progress", payload))
    buffered = json.loads(RUNTIME_EVENT_BUFFER[run_manager.runtime_event_buffer_key(task, run)][0])["event"]
    event_type = buffered["event_type"]
    message = buffered["message"]
    metadata = buffered["metadata"]

    assert event_type == "runtime_progress"
    assert message == "Read result received"
    assert "\\u0000" not in json.dumps(normalized, allow_nan=False)
    persisted = json.dumps(metadata, allow_nan=False)
    assert "ELF" not in persisted
    assert "\\u0000" not in persisted
    assert len(persisted) < 65_536


def test_runtime_tool_call_removes_nul_from_message_metadata_and_run_history():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))
    payload = {
        "status": "started",
        "data": {"tool_call_name": "Read\x00", "input": {"path": "notes.txt", "raw": "\x7fELF\x00payload"}},
    }

    normalized = asyncio.run(run_manager.record_runtime_event(task, run, "tool_call", payload))
    buffered = json.loads(RUNTIME_EVENT_BUFFER[run_manager.runtime_event_buffer_key(task, run)][0])["event"]
    message = buffered["message"]
    metadata = buffered["metadata"]

    assert "\x00" not in message
    assert "\\u0000" not in json.dumps(metadata, ensure_ascii=False, allow_nan=False)
    assert "\\u0000" not in json.dumps(run.tool_calls, ensure_ascii=False, allow_nan=False)
    assert "ELF" not in json.dumps(normalized, ensure_ascii=False, allow_nan=False)
    assert normalized["tool_call"]["data"]["input"]["path"] == "notes.txt"


def test_runtime_event_bounds_large_plain_text_without_changing_short_text():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))

    normalized = asyncio.run(run_manager.record_runtime_event(
        task,
        run,
        "progress",
        {"message": "ordinary status", "details": {"summary": "kept", "output": "x" * 200_000}},
    ))

    assert normalized["message"] == "ordinary status"
    assert normalized["metadata"]["payload"]["details"]["summary"] == "kept"
    buffered = json.loads(RUNTIME_EVENT_BUFFER[run_manager.runtime_event_buffer_key(task, run)][0])["event"]
    assert len(json.dumps(buffered["metadata"], allow_nan=False)) < 65_536


def test_runtime_event_converts_non_finite_numbers_before_jsonb_insert():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))

    normalized = asyncio.run(run_manager.record_runtime_event(
        task,
        run,
        "progress",
        {"metrics": {"nan": float("nan"), "infinity": float("inf")}},
    ))

    metrics = normalized["metadata"]["payload"]["metrics"]
    assert metrics == {"nan": None, "infinity": None}


def test_runtime_tool_call_history_keeps_recent_entries():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))
    run.tool_calls = [{"tool_name": str(index), "status": "started"} for index in range(100)]

    asyncio.run(run_manager.record_runtime_event(
        task, run, "tool_call", {"data": {"tool_call_name": "Read"}},
    ))

    assert len(run.tool_calls) == 100
    assert run.tool_calls[0]["tool_name"] == "1"
    assert run.tool_calls[-1]["tool_name"] == "Read"


def test_late_approval_event_is_ignored_after_run_is_terminal():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    run.status = "completed"
    initial_task_state = (task.status, task.run_id, list(task.saved_update_fields))
    initial_events = list(RECORDED_EVENTS)

    normalized = asyncio.run(
        run_manager.record_runtime_event(
            task,
            run,
            "approval_required",
            {"tool": "write_file", "risk_level": "high"},
        )
    )

    assert normalized is None
    assert (task.status, task.run_id, task.saved_update_fields) == initial_task_state
    assert RECORDED_EVENTS == initial_events
    assert sys.modules["models.command_center"].CommandApproval.created == []


def test_runtime_event_from_old_run_is_ignored_after_task_points_to_new_run():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    task.status = "running"
    task.run_id = "new-run-id"
    initial_heartbeat = run.heartbeat_at
    initial_events = list(RECORDED_EVENTS)

    normalized = asyncio.run(run_manager.record_runtime_event(task, run, "delta", {"content": "late"}))

    assert normalized is None
    assert task.status == "running"
    assert task.run_id == "new-run-id"
    assert run.heartbeat_at == initial_heartbeat
    assert run.saved_update_fields == []
    assert RECORDED_EVENTS == initial_events


def test_runtime_error_finalizes_task_through_progress_manager():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))

    normalized = asyncio.run(
        run_manager.record_runtime_event(
            task,
            run,
            "error",
            {"message": "runtime failed"},
            operator_id="operator-1",
        )
    )

    assert normalized["event_type"] == "runtime_error"
    assert task.status == "failed"
    assert UPDATED_PROGRESS[-1][0]["status"] == "failed"
    assert UPDATED_PROGRESS[-1][0]["metadata"]["run_id"] == run.run_id
    assert [event.event_type for event in RUNTIME_EVENT_BULK_CREATES[0]] == ["runtime_error"]


def test_persisted_runtime_error_code_survives_generic_wrapper_exception():
    run_manager = _load_run_manager()
    run = SimpleNamespace(
        error={
            "code": "context_compression_failed",
            "message": "上下文压缩失败，数字伙伴无法继续执行",
            "blocking": True,
        },
        metadata={},
    )

    layers = asyncio.run(
        run_manager._collect_failure_layers(run, RuntimeError("wrapper lost details"))
    )

    assert layers["runtime"]["code"] == "context_compression_failed"


def test_runtime_error_payload_method_preserves_specific_code():
    run_manager = _load_run_manager()

    class _StructuredRuntimeError(RuntimeError):
        def payload(self):
            return {
                "code": "context_compression_failed",
                "message": "上下文压缩失败，数字伙伴无法继续执行",
                "retryable": False,
            }

    error = run_manager._safe_runtime_error(_StructuredRuntimeError("opaque wrapper"))

    assert error["code"] == "context_compression_failed"
    assert error["blocking"] is True


def test_runtime_tool_call_normalization_accepts_agentscope_tool_call_name():
    run_manager = _load_run_manager()

    normalized = run_manager.normalize_runtime_event(
        "tool_call",
        {
            "status": "started",
            "data": {
                "tool_call_name": "aioe_cli",
                "input": {"argv": ["apps"]},
            },
        },
    )

    assert normalized["event_type"] == "runtime_tool_call"
    assert normalized["message"] == "aioe_cli started"
    assert normalized["tool_call"]["tool_name"] == "aioe_cli"


def test_denied_tool_result_is_recorded_without_failing_task():
    run_manager = _load_run_manager()
    task = _task()
    run = asyncio.run(run_manager.checkout_run(task, _employee()))

    normalized = asyncio.run(run_manager.record_runtime_event(
        task,
        run,
        "tool_error",
        {
            "data": {
                "type": "TOOL_RESULT_END",
                "tool_call_id": "read-xlsx",
                "tool_call_name": "Read",
                "state": "denied",
                "message": "Office documents must be read with the officecli tool",
            },
            "blocking": False,
            "retryable": True,
        },
    ))

    assert normalized["event_type"] == "runtime_tool_call"
    assert normalized["message"] == "Office documents must be read with the officecli tool"
    assert run.tool_calls[-1]["status"] == "denied"
    assert run.status == "running"
    assert task.status == "running"
    buffered = json.loads(RUNTIME_EVENT_BUFFER[run_manager.runtime_event_buffer_key(task, run)][0])["event"]
    assert buffered["event_type"] == "runtime_tool_call"


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

    query_filters = {}

    class Query:
        def filter(self, **kwargs):
            return self

        def using_db(self, connection):
            return self

        def select_for_update(self, **kwargs):
            return self

        async def first(self):
            return run

        async def all(self):
            return [run]

    def filter_runs(cls, **kwargs):
        query_filters.update(kwargs)
        return Query()

    sys.modules["models.command_center"].CommandTaskRun.filter = classmethod(filter_runs)

    recovered = asyncio.run(run_manager.recover_stale_runs(now=datetime.now(timezone.utc)))

    assert recovered == [run]
    assert run.status == "stale"
    assert run.error["message"] == "run lease expired"
    assert "waiting_approval" not in query_filters["status__in"]
    assert UPDATED_PROGRESS[-1][0]["status"] == "failed"
    assert RECORDED_EVENTS[-1][0] == "run_stale_recovered"


def test_stale_recovery_does_not_overwrite_terminal_task():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    task.status = "completed"
    run.task = _async_value(task)
    run.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)

    class Query:
        def filter(self, **kwargs):
            return self

        def using_db(self, connection):
            return self

        def select_for_update(self, **kwargs):
            return self

        async def first(self):
            return run

        async def all(self):
            return [run]

    sys.modules["models.command_center"].CommandTaskRun.filter = classmethod(lambda cls, **kwargs: Query())

    recovered = asyncio.run(run_manager.recover_stale_runs(now=datetime.now(timezone.utc)))

    assert recovered == [run]
    assert task.status == "completed"
    assert UPDATED_PROGRESS == []
    assert run.status == "stale"


def test_stale_recovery_skips_candidate_renewed_before_lock():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    run.task = _async_value(task)
    cutoff = datetime.now(timezone.utc)
    run.lease_expires_at = cutoff - timedelta(minutes=10)
    lock_options = []
    lease_renewed = False

    class Query:
        def __init__(self, filters):
            self.filters = filters

        def filter(self, **kwargs):
            self.filters.update(kwargs)
            return self

        def using_db(self, connection):
            return self

        def select_for_update(self, **kwargs):
            lock_options.append(kwargs)
            return self

        async def first(self):
            nonlocal lease_renewed
            lease_renewed = True
            run.lease_expires_at = cutoff + timedelta(minutes=5)
            if run.lease_expires_at >= self.filters["lease_expires_at__lt"]:
                return None
            return run

        async def all(self):
            return [run]

    sys.modules["models.command_center"].CommandTaskRun.filter = classmethod(lambda cls, **kwargs: Query(kwargs))

    recovered = asyncio.run(run_manager.recover_stale_runs(now=cutoff))

    assert lease_renewed is True
    assert lock_options == [{"skip_locked": True}]
    assert recovered == []
    assert run.status == "checked_out"
    assert task.status == "running"
    assert UPDATED_PROGRESS == []
    assert RECORDED_EVENTS == [("run_checked_out", "数字伙伴运行已 checkout", None, {
        "run_id": run.run_id,
        "lease_owner": run.lease_owner,
        "workspace_etag": run.workspace_etag,
    })]


def test_orphaned_dispatched_task_without_run_fails_and_releases_agent_load():
    run_manager = _load_run_manager()
    current = datetime.now(timezone.utc)
    task = _task()
    task.utime = current - timedelta(minutes=10)
    task.assigned_agent_key = "agent-1"
    task.assigned_agent = SimpleNamespace(
        load=1,
        max_concurrent=1,
        current_task_id=task.id,
        status="busy",
    )

    recovered = asyncio.run(run_manager.recover_orphaned_dispatched_tasks(now=current))

    assert recovered == [task]
    assert task.status == "failed"
    assert task.assigned_agent.load == 0
    assert task.assigned_agent.current_task_id is None
    assert task.assigned_agent.status == "idle"
    assert UPDATED_PROGRESS[-1][0]["metadata"] == {
        "reason": "orphaned_dispatched_task",
        "run_id": None,
        "run_status": None,
        "timeout_seconds": 300,
    }


@pytest.mark.parametrize(
    "run_status",
    ["checked_out", "running", "waiting_approval"],
)
def test_orphan_recovery_keeps_dispatched_task_with_current_run(run_status):
    run_manager = _load_run_manager()
    current = datetime.now(timezone.utc)
    task = _task()
    task.utime = current - timedelta(minutes=10)
    task.run_id = "run-1"
    run = sys.modules["models.command_center"].CommandTaskRun(
        id="run-row-1",
        tenant_id=task.tenant_id,
        task_id=task.id,
        run_id=task.run_id,
        status=run_status,
        deleted=False,
    )
    CREATED_RUNS.append(run)

    recovered = asyncio.run(run_manager.recover_orphaned_dispatched_tasks(now=current))

    assert recovered == []
    assert task.status == "dispatched"
    assert task.run_id == "run-1"
    assert UPDATED_PROGRESS == []


def test_orphan_recovery_fails_timed_out_continuation_ready_task_and_releases_load():
    run_manager = _load_run_manager()
    current = datetime.now(timezone.utc)
    task = _task()
    task.utime = current - timedelta(minutes=10)
    task.run_id = "run-1"
    task.assigned_agent_key = "agent-1"
    task.assigned_agent = SimpleNamespace(
        load=1,
        max_concurrent=1,
        current_task_id=task.id,
        status="busy",
    )
    run = sys.modules["models.command_center"].CommandTaskRun(
        id="run-row-1",
        tenant_id=task.tenant_id,
        task_id=task.id,
        run_id=task.run_id,
        status="continuation_ready",
        deleted=False,
        finished_at=None,
        heartbeat_at=current - timedelta(minutes=10),
        lease_owner="worker-1",
        lease_expires_at=current - timedelta(minutes=5),
        error={},
        saved_update_fields=[],
    )
    CREATED_RUNS.append(run)

    recovered = asyncio.run(run_manager.recover_orphaned_dispatched_tasks(now=current))

    assert recovered == [task]
    assert task.status == "failed"
    assert task.assigned_agent.load == 0
    assert task.assigned_agent.current_task_id is None
    assert task.assigned_agent.status == "idle"
    assert UPDATED_PROGRESS[-1][0]["metadata"]["run_status"] == "continuation_ready"
    assert run.status == "failed"
    assert run.finished_at == current
    assert run.heartbeat_at == current
    assert run.lease_owner is None
    assert run.lease_expires_at == current
    assert run.error == {
        "message": "task did not enter a valid continuation run",
        "reason": "orphaned_dispatched_task",
    }
    assert run.saved_update_fields == [[
        "status",
        "finished_at",
        "heartbeat_at",
        "lease_owner",
        "lease_expires_at",
        "error",
        "utime",
    ]]


def _continuation_ready_orphan_case(current, recovery):
    task = _task()
    task.utime = current - timedelta(minutes=10)
    task.run_id = "run-1"
    task.assigned_agent_key = "agent-1"
    task.assigned_agent = SimpleNamespace(
        load=1,
        max_concurrent=1,
        current_task_id=task.id,
        status="busy",
    )
    run = sys.modules["models.command_center"].CommandTaskRun(
        id="run-row-1",
        tenant_id=task.tenant_id,
        task_id=task.id,
        run_id=task.run_id,
        status="continuation_ready",
        deleted=False,
        finished_at=None,
        heartbeat_at=current - timedelta(minutes=10),
        lease_owner="worker-1",
        lease_expires_at=current - timedelta(minutes=5),
        error={},
        saved_update_fields=[],
    )
    approval = sys.modules["models.command_center"].CommandApproval(
        id="approval-1",
        tenant_id=task.tenant_id,
        task_id=task.id,
        run_id=run.id,
        status="approved",
        deleted=False,
        ctime=current,
        metadata={"continuation_recovery": recovery},
    )
    CREATED_RUNS.append(run)
    sys.modules["models.command_center"].CommandApproval.records = [approval]
    return task, run


def test_orphan_recovery_keeps_continuation_ready_task_with_active_resume_claim():
    run_manager = _load_run_manager()
    current = datetime.now(timezone.utc)
    task, run = _continuation_ready_orphan_case(
        current,
        {"status": "running", "claimed_at": current.isoformat()},
    )

    recovered = asyncio.run(run_manager.recover_orphaned_dispatched_tasks(now=current))

    assert recovered == []
    assert task.status == "dispatched"
    assert task.run_id == run.run_id
    assert run.status == "continuation_ready"
    assert run.saved_update_fields == []
    assert task.assigned_agent.load == 1
    assert task.assigned_agent.current_task_id == task.id
    assert task.assigned_agent.status == "busy"
    assert UPDATED_PROGRESS == []


@pytest.mark.parametrize(
    "recovery",
    [
        pytest.param(
            {"status": "running", "claimed_at": "expired"},
            id="expired-running-claim",
        ),
        pytest.param(
            {"status": "failed", "claimed_at": "failed"},
            id="failed-claim",
        ),
    ],
)
def test_orphan_recovery_terminates_continuation_ready_task_without_active_resume_claim(recovery):
    run_manager = _load_run_manager()
    current = datetime.now(timezone.utc)
    if recovery["status"] == "running":
        recovery["claimed_at"] = (current - timedelta(minutes=6)).isoformat()
    else:
        recovery["claimed_at"] = current.isoformat()
    task, run = _continuation_ready_orphan_case(current, recovery)

    recovered = asyncio.run(run_manager.recover_orphaned_dispatched_tasks(now=current))

    assert recovered == [task]
    assert task.status == "failed"
    assert run.status == "failed"
    assert run.finished_at == current
    assert run.heartbeat_at == current
    assert run.lease_owner is None
    assert run.lease_expires_at == current
    assert run.error == {
        "message": "task did not enter a valid continuation run",
        "reason": "orphaned_dispatched_task",
    }
    assert task.assigned_agent.load == 0
    assert task.assigned_agent.current_task_id is None
    assert task.assigned_agent.status == "idle"
    assert UPDATED_PROGRESS[-1][0]["metadata"]["run_status"] == "continuation_ready"


def test_finalize_run_persists_usage():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    usage = {"fallback": False, "input_tokens": 7, "output_tokens": 3, "total_tokens": 10, "model_calls": 1}

    asyncio.run(run_manager.finalize_run(run, "completed", result={"content": "ok"}, usage=usage))

    assert run.usage == usage
    assert "usage" in run.saved_update_fields[-1]


def test_finalize_run_does_not_overwrite_rejected_run():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    run.status = "rejected"

    asyncio.run(run_manager.finalize_run(run, "completed", result={"content": "late"}))

    assert run.status == "rejected"
    assert "result" not in run.metadata
    assert run.saved_update_fields == []
    assert FINALIZED_SAVES == []


def test_finalize_run_refreshes_metadata_before_merging_result():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    assert "linked_run_ids" not in run.metadata
    PERSISTED_RUN_METADATA[run.id] = {**run.metadata, "linked_run_ids": ["debug-run-1"]}

    result = {"content": "ok"}
    asyncio.run(run_manager.finalize_run(run, "completed", result=result))

    assert run.refreshed_fields == [["metadata"]]
    assert run.metadata["linked_run_ids"] == ["debug-run-1"]
    assert run.metadata["result"] == result
    assert PERSISTED_RUN_METADATA[run.id] == run.metadata


def test_finalize_run_without_result_preserves_refreshed_metadata():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    run = asyncio.run(run_manager.checkout_run(task, employee))
    persisted_metadata = {**run.metadata, "linked_run_ids": ["debug-run-1"]}
    PERSISTED_RUN_METADATA[run.id] = persisted_metadata

    asyncio.run(run_manager.finalize_run(run, "failed", error={"message": "failed"}))

    assert run.refreshed_fields == [["metadata"]]
    assert run.metadata == persisted_metadata
    assert PERSISTED_RUN_METADATA[run.id] == persisted_metadata


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


@pytest.mark.parametrize("decision", ["deny", "approval_required"])
def test_execute_task_run_preserves_pre_run_policy_stop(monkeypatch, decision):
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    finalized = []
    runtime_started = []

    async def enforce_pre_run_policy(task_arg, run_arg, **_kwargs):
        task_arg.status = "failed" if decision == "deny" else "pending_approval"
        run_arg.status = "failed" if decision == "deny" else "waiting_approval"
        return {"decision": decision}

    async def finalize(*args, **kwargs):
        finalized.append((args, kwargs))

    async def run(*args, **kwargs):
        runtime_started.append(True)
        return {"content": "unexpected"}

    monkeypatch.setattr(
        sys.modules["services.command_center.approval_service"],
        "enforce_pre_run_policy", enforce_pre_run_policy,
    )
    monkeypatch.setattr(run_manager, "finalize_run_and_task", finalize)
    execute_coordinator = types.ModuleType("services.digital_employee.execute_coordinator")
    execute_coordinator.run = run
    sys.modules["services.digital_employee.execute_coordinator"] = execute_coordinator

    result = asyncio.run(run_manager.execute_task_run(task, employee))

    assert result["policy"] == decision
    assert runtime_started == []
    assert finalized == []
    assert task.status == ("failed" if decision == "deny" else "pending_approval")
    assert CREATED_RUNS[-1].status == ("failed" if decision == "deny" else "waiting_approval")


def test_execute_task_run_renews_lease_during_silent_execution():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    execute_coordinator = types.ModuleType("services.digital_employee.execute_coordinator")
    heartbeat_observed = False

    async def run(task_arg, employee_arg, **kwargs):
        nonlocal heartbeat_observed
        command_run = kwargs["command_run"]
        for _ in range(20):
            await asyncio.sleep(0)
            heartbeat_observed = any(
                update_fields and "lease_expires_at" in update_fields
                for update_fields in command_run.saved_update_fields
            )
            if heartbeat_observed:
                break
        return {"content": "done", "usage": {}}

    execute_coordinator.run = run
    sys.modules["services.digital_employee.execute_coordinator"] = execute_coordinator
    run_manager.LEASE_HEARTBEAT_SECONDS = 0

    result = asyncio.run(run_manager.execute_task_run(task, employee))

    assert result["content"] == "done"
    assert heartbeat_observed is True
    assert CREATED_RUNS[-1].status == "completed"


def test_execute_task_run_fails_when_task_execution_deadline_expires():
    run_manager = _load_run_manager()
    task = _task()
    task.timeout_seconds = 1
    task.started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    employee = _employee()
    execute_coordinator = types.ModuleType("services.digital_employee.execute_coordinator")

    async def run(_task_arg, _employee_arg, **_kwargs):
        await asyncio.sleep(10)
        return {"content": "too late"}

    execute_coordinator.run = run
    sys.modules["services.digital_employee.execute_coordinator"] = execute_coordinator

    result = asyncio.run(run_manager.execute_task_run(task, employee))

    assert result["error"]["code"] == "task_timeout"
    assert task.status == "failed"
    assert CREATED_RUNS[-1].status == "failed"
    assert CREATED_RUNS[-1].error["code"] == "task_timeout"
    assert UPDATED_PROGRESS[-1][0]["metadata"]["code"] == "task_timeout"


def test_execute_task_run_does_not_misclassify_runtime_timeout_as_task_deadline():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    execute_coordinator = types.ModuleType("services.digital_employee.execute_coordinator")

    async def run(_task_arg, _employee_arg, **_kwargs):
        raise TimeoutError("one tool request timed out")

    execute_coordinator.run = run
    sys.modules["services.digital_employee.execute_coordinator"] = execute_coordinator

    with pytest.raises(TimeoutError, match="one tool request timed out"):
        asyncio.run(run_manager.execute_task_run(task, employee))

    assert task.status == "failed"
    assert CREATED_RUNS[-1].status == "failed"
    assert CREATED_RUNS[-1].error["code"] == "runtime_internal_error"
    assert UPDATED_PROGRESS[-1][0]["metadata"].get("code") != "task_timeout"


def test_task_deadline_covers_run_setup_and_releases_employee_load_once():
    run_manager = _load_run_manager()
    task = _task()
    task.timeout_seconds = 1
    task.started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    employee = _employee()
    employee.load = 1
    employee.status = "busy"
    employee.current_task_id = task.id
    task.assigned_agent = employee
    setup_cancelled = []
    runtime_started = []

    async def setup(_run):
        try:
            await asyncio.Event().wait()
        finally:
            setup_cancelled.append(True)

    execute_coordinator = types.ModuleType("services.digital_employee.execute_coordinator")

    async def run(_task_arg, _employee_arg, **_kwargs):
        runtime_started.append(True)
        return {"content": "unexpected"}

    execute_coordinator.run = run
    sys.modules["services.digital_employee.execute_coordinator"] = execute_coordinator
    result = asyncio.run(run_manager.execute_task_run(task, employee, on_run_checked_out=setup))

    assert result["error"]["code"] == "task_timeout"
    assert setup_cancelled == [True]
    assert runtime_started == []
    assert employee.load == 0
    assert employee.current_task_id is None
    assert employee.status == "idle"
    asyncio.run(run_manager.finalize_run_and_task(
        CREATED_RUNS[-1], task, "failed", {"status": "failed"},
    ))
    assert employee.load == 0


def test_maintain_run_lease_retries_after_heartbeat_database_error():
    run_manager = _load_run_manager()
    run = SimpleNamespace(run_id="run-1", status="running")
    attempts = []

    async def heartbeat_run(run_arg):
        attempts.append(run_arg.run_id)
        if len(attempts) == 1:
            raise RuntimeError("database unavailable")
        run_arg.status = "completed"
        return run_arg

    run_manager.heartbeat_run = heartbeat_run
    run_manager.LEASE_HEARTBEAT_SECONDS = 0

    asyncio.run(run_manager._maintain_run_lease(run))

    assert attempts == ["run-1", "run-1"]
    assert run.status == "completed"


def test_execute_task_run_records_real_continuation_start_on_new_run():
    run_manager = _load_run_manager()
    task = _task()
    employee = _employee()
    execute_coordinator = types.ModuleType("services.digital_employee.execute_coordinator")

    async def run(task_arg, employee_arg, **kwargs):
        return {
            "content": "done",
            "usage": {
                "fallback": False,
                "input_tokens": 11,
                "output_tokens": 4,
                "total_tokens": 15,
                "model_calls": 1,
            },
        }

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
    assert CREATED_RUNS[-1].usage["total_tokens"] == 15
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


def test_retry_run_creates_new_run_and_reuses_previous_session():
    run_manager = _load_run_manager()
    task = _task()
    task.status = "failed"
    task.run_id = "old-run"
    employee = _employee()
    previous = sys.modules["models.command_center"].CommandTaskRun(
        id="old-row",
        tenant_id=task.tenant_id,
        task_id=task.id,
        run_id="old-run",
        employee_id=employee.id,
        session_id="stable-session",
        status="failed",
        deleted=False,
    )
    previous.task = _async_value(task)
    previous.employee = _async_value(employee)
    CREATED_RUNS.append(previous)
    execute_coordinator = types.ModuleType("services.digital_employee.execute_coordinator")

    async def run(task_arg, employee_arg, **kwargs):
        assert kwargs["command_run"].session_id == "stable-session"
        return {"content": "retried", "usage": {}}

    execute_coordinator.run = run
    sys.modules["services.digital_employee.execute_coordinator"] = execute_coordinator

    result = asyncio.run(run_manager.retry_run(previous, operator_id="operator-1"))

    assert result["content"] == "retried"
    assert len(CREATED_RUNS) == 2
    assert CREATED_RUNS[-1].run_id != previous.run_id
    assert CREATED_RUNS[-1].session_id == "stable-session"
    assert employee.load == 1
    assert employee.current_task_id == task.id
    assert employee.status == "busy"


def test_prepare_run_retry_claims_once_and_restores_employee_workload():
    run_manager = _load_run_manager()
    task = _task()
    task.status = "failed"
    task.run_id = "old-run"
    task.result = "old failure"
    task.started_at = datetime.now(timezone.utc)
    task.finished_at = datetime.now(timezone.utc)
    employee = _employee()
    previous = sys.modules["models.command_center"].CommandTaskRun(
        id="old-row",
        tenant_id=task.tenant_id,
        task_id=task.id,
        run_id="old-run",
        session_id=None,
        employee_id=employee.id,
        status="failed",
        deleted=False,
    )
    previous.task = _async_value(task)
    previous.employee = _async_value(employee)
    CREATED_RUNS.append(previous)

    prepared_task, prepared_employee = asyncio.run(
        run_manager.prepare_run_retry(previous, operator_id="operator-1")
    )

    assert prepared_task.status == "dispatched"
    assert prepared_task.run_id is None
    assert prepared_task.result is None
    assert prepared_task.started_at is None
    assert prepared_task.finished_at is None
    assert prepared_employee.load == 1
    assert prepared_employee.current_task_id == task.id
    assert prepared_employee.status == "busy"
    assert RECORDED_EVENTS[-1][0] == "task_retry_requested"
    assert RECORDED_EVENTS[-1][3] == {
        "retried_from_run_id": "old-run",
        "session_id": "old-run",
    }

    with pytest.raises(run_manager.RunRetryConflict):
        asyncio.run(run_manager.prepare_run_retry(previous, operator_id="operator-1"))


def test_scheduled_retry_start_failure_restores_failed_state_and_employee_load(monkeypatch):
    run_manager = _load_run_manager()
    task = _task()
    task.status = "dispatched"
    task.assigned_agent_key = "ops"
    employee = _employee()
    employee.load = 1
    employee.status = "busy"
    employee.current_task_id = task.id
    task.assigned_agent = employee
    previous = sys.modules["models.command_center"].CommandTaskRun(
        id="old-row",
        tenant_id=task.tenant_id,
        task_id=task.id,
        run_id="old-run",
        session_id="stable-session",
        employee_id=employee.id,
        status="failed",
        deleted=False,
    )

    async def fail_retry(*_args, **_kwargs):
        raise RuntimeError("workspace sync failed")

    class TaskLookup:
        @classmethod
        async def get_or_none(cls, **kwargs):
            assert kwargs == {
                "id": task.id,
                "tenant_id": task.tenant_id,
                "deleted": False,
            }
            return task

    scheduled = []
    monkeypatch.setattr(run_manager, "retry_run", fail_retry)
    monkeypatch.setattr(run_manager, "CommandTask", TaskLookup)
    monkeypatch.setattr(run_manager.asyncio, "create_task", scheduled.append)

    run_manager.schedule_prepared_run_retry(
        previous,
        task,
        employee,
        operator_id="operator-1",
    )
    asyncio.run(scheduled.pop())

    assert task.status == "failed"
    assert employee.load == 0
    assert employee.current_task_id is None
    assert employee.status == "idle"
    assert UPDATED_PROGRESS[-1][0]["metadata"] == {
        "retried_from_run_id": "old-run",
    }
