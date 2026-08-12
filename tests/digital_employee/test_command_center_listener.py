# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types

import pytest


STUB_MODULES = [
    "sanic",
    "sanic.log",
    "core",
    "core.listeners",
    "core.listeners.base",
    "services",
    "services.command_center",
    "services.command_center.approval_service",
    "services.command_center.manager",
    "services.command_center.run_manager",
    "settings",
    "core.listeners.command_center",
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


def _load_listener():
    for name in STUB_MODULES:
        sys.modules.pop(name, None)
    sanic = types.ModuleType("sanic")
    sanic.Sanic = object
    sys.modules["sanic"] = sanic
    sanic_log = types.ModuleType("sanic.log")
    sanic_log.logger = SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
    sys.modules["sanic.log"] = sanic_log
    core = types.ModuleType("core")
    core.__path__ = []
    sys.modules["core"] = core
    listeners = types.ModuleType("core.listeners")
    listeners.__path__ = []
    sys.modules["core.listeners"] = listeners
    base_path = Path(__file__).resolve().parents[2] / "core/listeners/base.py"
    base_spec = importlib.util.spec_from_file_location("core.listeners.base", base_path)
    base_module = importlib.util.module_from_spec(base_spec)
    sys.modules["core.listeners.base"] = base_module
    base_spec.loader.exec_module(base_module)
    services = types.ModuleType("services")
    services.__path__ = []
    sys.modules["services"] = services
    command_center = types.ModuleType("services.command_center")
    command_center.__path__ = []
    sys.modules["services.command_center"] = command_center
    recovery_order = []
    approval_service = types.ModuleType("services.command_center.approval_service")
    approval_service.calls = []

    async def recover_pending_approval_resumes(operator_id=None):
        approval_service.calls.append(operator_id)
        recovery_order.append(f"approval:{operator_id}")
        if operator_id == "system-periodic-recovery":
            raise asyncio.CancelledError
        return [{"run_id": "run-2"}]

    approval_service.recover_pending_approval_resumes = recover_pending_approval_resumes
    sys.modules["services.command_center.approval_service"] = approval_service
    manager = types.ModuleType("services.command_center.manager")
    manager.workload_calls = []
    manager.recovery_order = recovery_order
    manager.orphan_calls = []
    manager.sweep_calls = []

    async def reconcile_agent_workloads(tenant_id=None):
        manager.workload_calls.append(tenant_id)
        manager.recovery_order.append("workload")
        return 1

    manager.reconcile_agent_workloads = reconcile_agent_workloads

    async def sweep_stale_dispatch_tasks(operator_id=None):
        manager.sweep_calls.append(operator_id)
        raise asyncio.CancelledError

    manager.sweep_stale_dispatch_tasks = sweep_stale_dispatch_tasks
    sys.modules["services.command_center.manager"] = manager
    run_manager = types.ModuleType("services.command_center.run_manager")

    async def recover_stale_runs(tenant_id=None, now=None):
        manager.recovery_order.append("stale_runs")
        return [SimpleNamespace(run_id="stale-run-1")]

    async def recover_orphaned_dispatched_tasks(tenant_id=None, now=None):
        manager.orphan_calls.append((tenant_id, now))
        manager.recovery_order.append("orphaned_tasks")
        return [SimpleNamespace(id="task-1")]

    run_manager.recover_stale_runs = recover_stale_runs
    run_manager.recover_orphaned_dispatched_tasks = recover_orphaned_dispatched_tasks
    sys.modules["services.command_center.run_manager"] = run_manager
    settings = types.ModuleType("settings")
    settings.COMMAND_TASK_SWEEP_INTERVAL_SECONDS = 0
    sys.modules["settings"] = settings
    path = Path(__file__).resolve().parents[2] / "core/listeners/command_center.py"
    spec = importlib.util.spec_from_file_location("core.listeners.command_center", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["core.listeners.command_center"] = module
    spec.loader.exec_module(module)
    return module, approval_service, manager, run_manager


class App:
    def __init__(self):
        self.tasks = {}

    def add_task(self, coro, name):
        self.tasks[name] = asyncio.create_task(coro)

    async def cancel_task(self, name):
        task = self.tasks.get(name)
        if task and not task.done():
            task.cancel()


def test_command_center_listener_runs_startup_resume_recovery():
    module, approval_service, manager, _run_manager = _load_listener()

    async def scenario():
        app = App()
        await module.CommandCenterListener().before_server_start(app, asyncio.get_running_loop())
        await module.CommandCenterListener().after_server_start(app, asyncio.get_running_loop())
        await app.tasks["command_center_approval_resume_recovery"]
        await app.tasks["command_center_dispatch_timeout_sweeper"]

    asyncio.run(scenario())

    assert approval_service.calls == ["system-startup-recovery", "system-periodic-recovery"]
    assert manager.workload_calls == [None, None]
    assert manager.recovery_order == [
        "stale_runs",
        "workload",
        "approval:system-startup-recovery",
        "approval:system-periodic-recovery",
        "stale_runs",
        "orphaned_tasks",
        "workload",
    ]
    assert manager.orphan_calls == [(None, None)]
    assert manager.sweep_calls == ["system-dispatch-timeout"]


def test_startup_reconciles_workloads_when_stale_recovery_fails():
    module, _approval_service, manager, run_manager = _load_listener()

    async def fail_stale_recovery(tenant_id=None, now=None):
        manager.recovery_order.append("stale_runs_failed")
        raise RuntimeError("database unavailable")

    run_manager.recover_stale_runs = fail_stale_recovery

    async def scenario():
        await module.CommandCenterListener().before_server_start(App(), asyncio.get_running_loop())

    asyncio.run(scenario())

    assert manager.recovery_order == ["stale_runs_failed", "workload"]
    assert manager.workload_calls == [None]
    assert manager.orphan_calls == []
