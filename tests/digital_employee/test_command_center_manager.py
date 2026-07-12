# coding=utf-8
import asyncio
from datetime import timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types

import pytest


STUB_MODULES = [
    "models",
    "models.command_center",
    "sanic",
    "sanic.log",
    "settings",
    "tortoise",
    "tortoise.expressions",
    "tortoise.transactions",
    "services",
    "services.command_center",
    "services.command_center.dispatcher",
    "services.command_center.realtime",
    "services.command_center.run_manager",
    "services.digital_employee",
    "services.digital_employee.employee_report",
    "services.command_center.manager_boundary_test",
]
_MISSING_MODULE = object()
LOCKED_TASK = None


@pytest.fixture(autouse=True)
def _restore_stubbed_modules():
    original_modules = {name: sys.modules.get(name, _MISSING_MODULE) for name in STUB_MODULES}
    yield
    for name in STUB_MODULES:
        original = original_modules[name]
        if original is _MISSING_MODULE:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


def _load_manager(agent, scheduled_reports):
    realtime_events = []

    class CommandTask:
        created = []

        class Query:
            def __init__(self, filters):
                self.filters = filters

            def using_db(self, connection):
                return self

            def select_for_update(self):
                return self

            def order_by(self, *args):
                return self

            async def first(self):
                return LOCKED_TASK

            async def all(self):
                return []

        @classmethod
        def filter(cls, **kwargs):
            return cls.Query(kwargs)

        @classmethod
        async def create(cls, **kwargs):
            task = FakeTask(
                id="created-task-1",
                tenant_id=kwargs["tenant_id"],
                title=kwargs["title"],
                description=kwargs.get("description"),
                source=kwargs.get("source", "manual"),
                priority=kwargs.get("priority", 3),
                status="pending_dispatch",
                progress=0,
                required_capabilities=kwargs.get("required_capabilities", []),
                payload=kwargs.get("payload", {}),
                requested_agent_id=kwargs.get("requested_agent_id"),
                assigned_agent_key=None,
                run_id=None,
                saved=False,
            )
            cls.created.append(task)
            return task

    class CommandTaskEvent:
        @classmethod
        async def create(cls, **kwargs):
            return SimpleNamespace(**kwargs)

    class CommandAgent:
        class Query:
            def __init__(self, filters):
                self.filters = filters

            def using_db(self, connection):
                return self

            def select_for_update(self):
                return self

            def order_by(self, *args):
                return self

            async def first(self):
                return agent

            async def all(self):
                return []

        @classmethod
        async def get_or_create(cls, **kwargs):
            return agent, False

        @classmethod
        async def get_or_none(cls, **kwargs):
            return agent

        @classmethod
        def filter(cls, **kwargs):
            return cls.Query(kwargs)

    class Q:
        def __init__(self, *args, **kwargs):
            pass

        def __and__(self, other):
            return self

        def __or__(self, other):
            return self

    async def compose(task, actions_draft=None):
        return SimpleNamespace(task=task, actions_draft=actions_draft)

    def create_task(coro):
        scheduled_reports.append(coro)
        coro.close()
        return SimpleNamespace()

    for name in STUB_MODULES:
        sys.modules.pop(name, None)

    sys.modules["models"] = types.ModuleType("models")
    command_center_module = types.ModuleType("models.command_center")
    command_center_module.CommandAgent = CommandAgent
    command_center_module.CommandTask = CommandTask
    command_center_module.CommandTaskEvent = CommandTaskEvent
    sys.modules["models.command_center"] = command_center_module

    sys.modules["sanic"] = types.ModuleType("sanic")
    sanic_log_module = types.ModuleType("sanic.log")
    sanic_log_module.logger = SimpleNamespace(exception=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
    sys.modules["sanic.log"] = sanic_log_module

    settings_module = types.ModuleType("settings")
    settings_module.COMMAND_TASK_DISPATCH_TIMEOUT_SECONDS = 300
    settings_module.TZ = timezone.utc
    sys.modules["settings"] = settings_module

    tortoise_module = types.ModuleType("tortoise")
    tortoise_module.__path__ = []
    sys.modules["tortoise"] = tortoise_module
    expressions_module = types.ModuleType("tortoise.expressions")
    expressions_module.Q = Q
    sys.modules["tortoise.expressions"] = expressions_module
    transactions_module = types.ModuleType("tortoise.transactions")

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    transactions_module.in_transaction = Transaction
    sys.modules["tortoise.transactions"] = transactions_module

    services_module = types.ModuleType("services")
    services_module.__path__ = []
    sys.modules["services"] = services_module
    command_center_pkg = types.ModuleType("services.command_center")
    command_center_pkg.__path__ = []
    sys.modules["services.command_center"] = command_center_pkg
    dispatcher_module = types.ModuleType("services.command_center.dispatcher")
    dispatcher_module.choose_agent_with_lion_agent = lambda task, agents, coordinator: _async_value(SimpleNamespace(agent=None))
    sys.modules["services.command_center.dispatcher"] = dispatcher_module
    realtime_module = types.ModuleType("services.command_center.realtime")

    async def publish_command_center_update(tenant_id, reason, entity_id=None):
        realtime_events.append((tenant_id, reason, entity_id))

    realtime_module.publish_command_center_update = publish_command_center_update
    sys.modules["services.command_center.realtime"] = realtime_module

    digital_employee_pkg = types.ModuleType("services.digital_employee")
    digital_employee_pkg.__path__ = []
    sys.modules["services.digital_employee"] = digital_employee_pkg
    employee_report_module = types.ModuleType("services.digital_employee.employee_report")
    employee_report_module.compose = compose
    sys.modules["services.digital_employee.employee_report"] = employee_report_module

    path = Path(__file__).resolve().parents[2] / "services/command_center/manager.py"
    spec = importlib.util.spec_from_file_location("services.command_center.manager_boundary_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["services.command_center.manager_boundary_test"] = module
    spec.loader.exec_module(module)
    module.asyncio = SimpleNamespace(create_task=create_task)
    module.realtime_events = realtime_events
    return module


async def _async_value(value):
    return value


class FakeAgent(SimpleNamespace):
    async def save(self, using_db=None, update_fields=None):
        self.saved_update_fields.append(update_fields)


class FakeTask(SimpleNamespace):
    async def save(self, using_db=None, update_fields=None):
        self.saved = True
        self.saved_update_fields = getattr(self, "saved_update_fields", [])
        self.saved_update_fields.append(update_fields)

    @property
    def json(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "progress": self.progress,
            "required_capabilities": self.required_capabilities,
            "payload": self.payload,
            "requested_agent_id": self.requested_agent_id,
            "assigned_agent_key": self.assigned_agent_key,
            "run_id": self.run_id,
        }


def _task():
    global LOCKED_TASK
    LOCKED_TASK = FakeTask(
        id="task-1",
        tenant_id="tenant-1",
        status="running",
        progress=20,
        assigned_agent_key="agent-1",
        started_at=None,
        finished_at=None,
        run_id=None,
        result=None,
        saved=False,
    )
    return LOCKED_TASK


def _agent(role_key):
    return FakeAgent(
        runtime="lion_agent",
        role_key=role_key,
        load=1,
        current_task_id="task-1",
        status="busy",
        max_concurrent=2,
        saved_update_fields=[],
    )


def test_update_task_progress_schedules_employee_report_for_digital_employee():
    scheduled_reports = []
    agent = _agent("ops")
    manager = _load_manager(agent, scheduled_reports)
    task = _task()

    asyncio.run(manager.update_task_progress(task, {"status": "completed", "metadata": {"actions": [{"name": "check"}]}}))

    assert task.progress == 100
    assert task.finished_at is not None
    assert agent.load == 0
    assert agent.current_task_id is None
    assert agent.status == "idle"
    assert manager.realtime_events == [("tenant-1", "task_progress_updated", "task-1")]
    assert len(scheduled_reports) == 1


def test_update_task_progress_releases_employee_only_once():
    scheduled_reports = []
    agent = _agent("ops")
    manager = _load_manager(agent, scheduled_reports)
    task = _task()
    stale_caller = FakeTask(**task.__dict__)

    asyncio.run(manager.update_task_progress(task, {"status": "failed"}))
    first_finished_at = task.finished_at
    asyncio.run(manager.update_task_progress(stale_caller, {"status": "completed"}))

    assert agent.load == 0
    assert agent.current_task_id is None
    assert agent.status == "idle"
    assert len(agent.saved_update_fields) == 1
    assert task.status == "failed"
    assert stale_caller.status == "failed"
    assert task.finished_at == first_finished_at
    assert stale_caller.finished_at == first_finished_at
    assert len(scheduled_reports) == 1
    assert manager.realtime_events == [
        ("tenant-1", "task_progress_updated", "task-1"),
        ("tenant-1", "task_progress_updated", "task-1"),
    ]


def test_reconcile_agent_workloads_uses_active_tasks_as_source_of_truth():
    scheduled_reports = []
    stale_agent = FakeAgent(
        id="agent-stale",
        tenant_id="tenant-1",
        status="busy",
        load=1,
        max_concurrent=1,
        current_task_id="deleted-task",
        saved_update_fields=[],
    )
    active_agent = FakeAgent(
        id="agent-active",
        tenant_id="tenant-1",
        status="idle",
        load=0,
        max_concurrent=1,
        current_task_id=None,
        saved_update_fields=[],
    )
    manager = _load_manager(stale_agent, scheduled_reports)

    agents = [stale_agent, active_agent]

    class AgentQuery:
        def __init__(self, filters):
            self.filters = filters

        def select_for_update(self):
            return self

        async def first(self):
            return next((item for item in agents if item.id == self.filters.get("id")), None)

        async def all(self):
            return agents

    class TaskQuery:
        def __init__(self, filters):
            self.filters = filters

        def exclude(self, **kwargs):
            return self

        def order_by(self, *args):
            return self

        async def all(self):
            if self.filters.get("assigned_agent_id") == "agent-active":
                return [SimpleNamespace(id="active-task", assigned_agent_id="agent-active")]
            return []

    manager.CommandAgent.filter = classmethod(lambda cls, **kwargs: AgentQuery(kwargs))
    manager.CommandTask.filter = classmethod(lambda cls, **kwargs: TaskQuery(kwargs))

    reconciled = asyncio.run(manager.reconcile_agent_workloads("tenant-1"))

    assert reconciled == 2
    assert (stale_agent.status, stale_agent.load, stale_agent.current_task_id) == ("idle", 0, None)
    assert (active_agent.status, active_agent.load, active_agent.current_task_id) == ("busy", 1, "active-task")
    assert manager.realtime_events == [("tenant-1", "agent_workloads_reconciled", None)]


def test_stale_dispatch_sweep_does_not_fail_task_advanced_before_lock():
    manager = _load_manager(_agent("ops"), [])
    task = _task()
    task.status = "pending_dispatch"
    task.ctime = None
    lock_options = []

    class Query:
        def using_db(self, connection):
            return self

        def select_for_update(self, **kwargs):
            lock_options.append(kwargs)
            return self

        async def all(self):
            return [task]

        async def first(self):
            task.status = "dispatched"
            return None

    manager.CommandTask.filter = classmethod(lambda cls, **kwargs: Query())

    swept = asyncio.run(manager.sweep_stale_dispatch_tasks(operator_id="system-dispatch-timeout"))

    assert swept == 0
    assert lock_options == [{"skip_locked": True}]
    assert task.status == "dispatched"
    assert task.saved is False
    assert manager.realtime_events == []


def test_dispatch_task_does_not_assign_candidate_that_became_full_before_lock():
    scheduled_tasks = []
    candidate = FakeAgent(
        id="agent-1",
        tenant_id="tenant-1",
        agent_key="agent-1",
        status="busy",
        load=1,
        max_concurrent=1,
        current_task_id="other-task",
        runtime="lion_agent",
        role_key="ops",
        saved_update_fields=[],
    )
    manager = _load_manager(candidate, scheduled_tasks)
    task = _task()
    task.status = "pending_dispatch"
    task.assigned_agent_key = None
    task.dispatch_status = "pending"
    task.dispatch_reason = None
    task.assigned_agent = None

    async def ensure_coordinator(tenant_id):
        return SimpleNamespace(id="coordinator-1")

    async def choose_agent(task_arg, agents, coordinator):
        return SimpleNamespace(
            agent=candidate,
            dispatch_status="selected",
            reason="能力匹配",
            raw_response={"agent_key": candidate.agent_key},
        )

    class TaskQuery:
        def using_db(self, connection):
            return self

        def select_for_update(self):
            return self

        async def first(self):
            return task

    class AgentQuery:
        def using_db(self, connection):
            return self

        def select_for_update(self):
            return self

        def order_by(self, *args):
            return self

        async def all(self):
            return [candidate]

    manager.ensure_default_coordinator_agent = ensure_coordinator
    manager.choose_agent_with_lion_agent = choose_agent
    manager.CommandTask.filter = classmethod(lambda cls, **kwargs: TaskQuery())
    manager.CommandAgent.filter = classmethod(lambda cls, **kwargs: AgentQuery())

    dispatched = asyncio.run(manager.dispatch_task(task, operator_id="operator-1"))

    assert dispatched is task
    assert task.status == "pending_dispatch"
    assert task.assigned_agent_key is None
    assert task.dispatch_status == "no_candidate"
    assert task.dispatch_reason == "候选员工容量已变化，请重新调度"
    assert candidate.load == candidate.max_concurrent == 1
    assert candidate.current_task_id == "other-task"
    assert candidate.saved_update_fields == []
    assert manager.realtime_events == [("tenant-1", "task_dispatched", "task-1")]


@pytest.mark.parametrize("candidate_status", ["offline", "error"])
def test_dispatch_task_does_not_keep_same_assignment_for_unavailable_candidate(candidate_status):
    candidate = FakeAgent(
        id="agent-1",
        tenant_id="tenant-1",
        agent_key="agent-1",
        status=candidate_status,
        load=1,
        max_concurrent=2,
        current_task_id="task-1",
        runtime="lion_agent",
        role_key="ops",
        saved_update_fields=[],
    )
    manager = _load_manager(candidate, [])
    task = _task()
    task.status = "pending_dispatch"
    task.assigned_agent_key = candidate.agent_key
    task.assigned_agent = candidate
    task.dispatch_status = "pending"
    task.dispatch_reason = None

    async def ensure_coordinator(tenant_id):
        return SimpleNamespace(id="coordinator-1")

    async def choose_agent(task_arg, agents, coordinator):
        return SimpleNamespace(
            agent=candidate,
            dispatch_status="selected",
            reason="能力匹配",
            raw_response={"agent_key": candidate.agent_key},
        )

    class TaskQuery:
        def using_db(self, connection):
            return self

        def select_for_update(self):
            return self

        async def first(self):
            return task

    class AgentQuery:
        def using_db(self, connection):
            return self

        def select_for_update(self):
            return self

        def order_by(self, *args):
            return self

        async def all(self):
            return [candidate]

    manager.ensure_default_coordinator_agent = ensure_coordinator
    manager.choose_agent_with_lion_agent = choose_agent
    manager.CommandTask.filter = classmethod(lambda cls, **kwargs: TaskQuery())
    manager.CommandAgent.filter = classmethod(lambda cls, **kwargs: AgentQuery())

    dispatched = asyncio.run(manager.dispatch_task(task, operator_id="operator-1"))

    assert dispatched is task
    assert task.status == "pending_dispatch"
    assert task.assigned_agent_key is None
    assert task.assigned_agent is None
    assert task.dispatch_status == "no_candidate"
    assert candidate.status == candidate_status
    assert candidate.load == 0
    assert candidate.current_task_id is None
    assert candidate.saved_update_fields == [["load", "current_task_id", "status"]]


def test_scheduled_execution_ignores_checkout_conflict_without_failing_winner():
    employee = FakeAgent(
        id="agent-1",
        tenant_id="tenant-1",
        runtime="lion_agent",
        role_key="ops",
        saved_update_fields=[],
    )
    manager = _load_manager(employee, [])
    task = _task()
    task.status = "running"
    task.run_id = "winner-run"
    task.result = None
    scheduled = []

    manager.CommandTask.get_or_none = classmethod(lambda cls, **kwargs: _async_value(task))
    manager.asyncio.create_task = lambda coro: scheduled.append(coro) or SimpleNamespace()
    run_manager = types.ModuleType("services.command_center.run_manager")

    class RunCheckoutConflict(ValueError):
        pass

    async def execute_task_run(task_arg, employee_arg, operator_id=None):
        raise RunCheckoutConflict("task is not dispatchable")

    run_manager.RunCheckoutConflict = RunCheckoutConflict
    run_manager.execute_task_run = execute_task_run
    sys.modules["services.command_center.run_manager"] = run_manager

    manager._schedule_lion_agent_execution(task, employee, operator_id="operator-1")
    asyncio.run(scheduled[0])

    assert task.status == "running"
    assert task.run_id == "winner-run"
    assert task.result is None
    assert task.saved is False
    assert manager.realtime_events == []


def test_scheduled_execution_generic_loser_does_not_fail_task_won_by_other_runner():
    employee = FakeAgent(
        id="agent-1",
        tenant_id="tenant-1",
        runtime="lion_agent",
        role_key="ops",
        saved_update_fields=[],
    )
    manager = _load_manager(employee, [])
    task = _task()
    task.status = "dispatched"
    task.result = None
    scheduled = []

    manager.CommandTask.get_or_none = classmethod(lambda cls, **kwargs: _async_value(task))
    manager.asyncio.create_task = lambda coro: scheduled.append(coro) or SimpleNamespace()
    run_manager = types.ModuleType("services.command_center.run_manager")

    class RunCheckoutConflict(ValueError):
        pass

    async def execute_task_run(task_arg, employee_arg, operator_id=None):
        task_arg.status = "running"
        task_arg.run_id = "winner-run"
        raise RuntimeError("losing runner failed before checkout")

    run_manager.RunCheckoutConflict = RunCheckoutConflict
    run_manager.execute_task_run = execute_task_run
    sys.modules["services.command_center.run_manager"] = run_manager

    manager._schedule_lion_agent_execution(task, employee, operator_id="operator-1")
    asyncio.run(scheduled[0])

    assert task.status == "running"
    assert task.run_id == "winner-run"
    assert task.result is None
    assert task.saved is False
    assert manager.realtime_events == []


def test_scheduled_dispatch_generic_loser_does_not_fail_task_won_by_other_dispatcher():
    manager = _load_manager(_agent("ops"), [])
    task = _task()
    task.status = "pending_dispatch"
    task.result = None
    scheduled = []

    manager.CommandTask.get_or_none = classmethod(lambda cls, **kwargs: _async_value(task))
    manager.asyncio.create_task = lambda coro: scheduled.append(coro) or SimpleNamespace()

    async def dispatch_task(task_arg, operator_id=None):
        task_arg.status = "dispatched"
        task_arg.assigned_agent_key = "winner-agent"
        raise RuntimeError("losing dispatcher failed")

    manager.dispatch_task = dispatch_task

    manager._schedule_task_dispatch(task, operator_id="operator-1")
    asyncio.run(scheduled[0])

    assert task.status == "dispatched"
    assert task.assigned_agent_key == "winner-agent"
    assert task.result is None
    assert task.saved is False
    assert manager.realtime_events == []


def test_update_task_progress_skips_employee_report_for_plain_command_agent():
    scheduled_reports = []
    manager = _load_manager(_agent(None), scheduled_reports)
    task = _task()

    asyncio.run(manager.update_task_progress(task, {"status": "completed"}))

    assert task.progress == 100
    assert task.finished_at is not None
    assert scheduled_reports == []
    assert manager.realtime_events == [("tenant-1", "task_progress_updated", "task-1")]


def test_create_command_task_derives_title_from_full_description_and_returns_before_dispatch():
    scheduled_tasks = []
    manager = _load_manager(_agent("ops"), scheduled_tasks)
    creator = SimpleNamespace(id="operator-1")

    task = asyncio.run(
        manager.create_command_task(
            "tenant-1",
            creator,
            {
                "description": "托管云test1有多少机器\n请查询资源容量并给出结论",
                "required_capabilities": ["cloud_ops"],
            },
        )
    )

    assert task.title == "托管云test1有多少机器"
    assert task.description == "托管云test1有多少机器\n请查询资源容量并给出结论"
    assert task.status == "pending_dispatch"
    assert task.assigned_agent_key is None
    assert len(scheduled_tasks) == 1
    assert manager.realtime_events == [("tenant-1", "task_created", "created-task-1")]
