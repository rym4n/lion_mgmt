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
    "settings",
    "tortoise",
    "tortoise.expressions",
    "services",
    "services.command_center",
    "services.command_center.dispatcher",
    "services.digital_employee",
    "services.digital_employee.employee_report",
    "services.command_center.manager_boundary_test",
]
_MISSING_MODULE = object()


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
    class CommandTask:
        pass

    class CommandTaskEvent:
        @classmethod
        async def create(cls, **kwargs):
            return SimpleNamespace(**kwargs)

    class CommandAgent:
        @classmethod
        async def get_or_create(cls, **kwargs):
            return agent, False

        @classmethod
        async def get_or_none(cls, **kwargs):
            return agent

        @classmethod
        def filter(cls, **kwargs):
            return SimpleNamespace(all=lambda: _async_value([]))

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

    settings_module = types.ModuleType("settings")
    settings_module.TZ = timezone.utc
    sys.modules["settings"] = settings_module

    sys.modules["tortoise"] = types.ModuleType("tortoise")
    expressions_module = types.ModuleType("tortoise.expressions")
    expressions_module.Q = Q
    sys.modules["tortoise.expressions"] = expressions_module

    services_module = types.ModuleType("services")
    services_module.__path__ = []
    sys.modules["services"] = services_module
    command_center_pkg = types.ModuleType("services.command_center")
    command_center_pkg.__path__ = []
    sys.modules["services.command_center"] = command_center_pkg
    dispatcher_module = types.ModuleType("services.command_center.dispatcher")
    dispatcher_module.choose_agent_with_lion_agent = lambda task, agents, coordinator: _async_value(SimpleNamespace(agent=None))
    sys.modules["services.command_center.dispatcher"] = dispatcher_module

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
    return module


async def _async_value(value):
    return value


class FakeAgent(SimpleNamespace):
    async def save(self, update_fields=None):
        self.saved_update_fields.append(update_fields)


class FakeTask(SimpleNamespace):
    async def save(self):
        self.saved = True


def _task():
    return FakeTask(
        id="task-1",
        tenant_id="tenant-1",
        status="running",
        progress=20,
        assigned_agent_key="agent-1",
        started_at=None,
        finished_at=None,
        run_id=None,
        saved=False,
    )


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
    manager = _load_manager(_agent("ops"), scheduled_reports)
    task = _task()

    asyncio.run(manager.update_task_progress(task, {"status": "completed", "metadata": {"actions": [{"name": "check"}]}}))

    assert task.progress == 100
    assert task.finished_at is not None
    assert len(scheduled_reports) == 1


def test_update_task_progress_skips_employee_report_for_plain_command_agent():
    scheduled_reports = []
    manager = _load_manager(_agent(None), scheduled_reports)
    task = _task()

    asyncio.run(manager.update_task_progress(task, {"status": "completed"}))

    assert task.progress == 100
    assert task.finished_at is not None
    assert scheduled_reports == []
