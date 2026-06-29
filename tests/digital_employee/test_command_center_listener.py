# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types


def _load_listener():
    for name in [
        "sanic",
        "sanic.log",
        "core",
        "core.listeners",
        "core.listeners.base",
        "services",
        "services.command_center",
        "services.command_center.approval_service",
        "core.listeners.command_center",
    ]:
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
    approval_service = types.ModuleType("services.command_center.approval_service")
    approval_service.calls = []

    async def recover_pending_approval_resumes(operator_id=None):
        approval_service.calls.append(operator_id)
        return [{"run_id": "run-2"}]

    approval_service.recover_pending_approval_resumes = recover_pending_approval_resumes
    sys.modules["services.command_center.approval_service"] = approval_service
    path = Path(__file__).resolve().parents[2] / "core/listeners/command_center.py"
    spec = importlib.util.spec_from_file_location("core.listeners.command_center", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["core.listeners.command_center"] = module
    spec.loader.exec_module(module)
    return module, approval_service


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
    module, approval_service = _load_listener()

    async def scenario():
        app = App()
        await module.CommandCenterListener().after_server_start(app, asyncio.get_running_loop())
        await app.tasks["command_center_approval_resume_recovery"]

    asyncio.run(scenario())

    assert approval_service.calls == ["system-startup-recovery"]
