# coding=utf-8
import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


STUB_MODULES = [
    "sanic",
    "sanic.log",
    "services",
    "services.command_center",
    "services.command_center.dispatcher",
    "services.digital_employee",
    "services.digital_employee.lion_agent_adapter",
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


def _load_dispatcher():
    for name in STUB_MODULES:
        sys.modules.pop(name, None)

    sanic_module = types.ModuleType("sanic")
    sanic_log_module = types.ModuleType("sanic.log")
    sanic_log_module.logger = SimpleNamespace(error=lambda *args, **kwargs: None)
    sys.modules["sanic"] = sanic_module
    sys.modules["sanic.log"] = sanic_log_module

    services_module = types.ModuleType("services")
    services_module.__path__ = []
    sys.modules["services"] = services_module
    command_center_module = types.ModuleType("services.command_center")
    command_center_module.__path__ = []
    sys.modules["services.command_center"] = command_center_module
    digital_employee_module = types.ModuleType("services.digital_employee")
    digital_employee_module.__path__ = []
    sys.modules["services.digital_employee"] = digital_employee_module

    adapter = types.ModuleType("services.digital_employee.lion_agent_adapter")
    adapter.workspace_id_for = lambda tenant_id, *parts: f"{tenant_id}:{':'.join(parts)}"

    async def execute_prompt(**kwargs):
        raise AssertionError("available requested agents should not call lion_agent")

    adapter.execute_prompt = execute_prompt
    sys.modules["services.digital_employee.lion_agent_adapter"] = adapter

    path = Path(__file__).resolve().parents[2] / "services/command_center/dispatcher.py"
    spec = importlib.util.spec_from_file_location("services.command_center.dispatcher", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["services.command_center.dispatcher"] = module
    spec.loader.exec_module(module)
    return module


def _agent(agent_key: str, *, status: str = "idle", load: int = 0, max_concurrent: int = 1, role_key: str | None = None):
    return SimpleNamespace(
        id=f"id-{agent_key}",
        agent_key=agent_key,
        name=f"员工 {agent_key}",
        description=None,
        capabilities=[],
        status=status,
        load=load,
        max_concurrent=max_concurrent,
        metadata={"digital_employee": True} if role_key else {},
        runtime="lion_agent",
        is_builtin=False,
        role_key=role_key,
    )


def test_available_requested_agent_is_selected_without_lion_agent_call():
    dispatcher = _load_dispatcher()
    selected = _agent("ops-1")
    task = SimpleNamespace(
        id="task-1",
        tenant_id="tenant-1",
        title="巡检",
        description="检查磁盘",
        source="workflow",
        priority=3,
        status="pending_dispatch",
        progress=0,
        required_capabilities=[],
        payload={},
        requested_agent_id="ops-1",
        room_id=None,
        creator_id=None,
    )

    decision = asyncio.run(dispatcher.choose_agent_with_lion_agent(task, [selected], coordinator=None))

    assert decision.agent is selected
    assert decision.dispatch_status == "user_specified"
    assert decision.requested_agent_id == "ops-1"


def test_digital_employee_scope_ignores_plain_command_agent():
    dispatcher = _load_dispatcher()
    plain_agent = _agent("ops-1")
    task = SimpleNamespace(
        id="task-1",
        tenant_id="tenant-1",
        title="巡检",
        description="检查磁盘",
        source="workflow",
        priority=3,
        status="pending_dispatch",
        progress=0,
        required_capabilities=[],
        payload={"_dispatch_scope": "digital_employee"},
        requested_agent_id="ops-1",
        room_id=None,
        creator_id=None,
    )

    decision = asyncio.run(dispatcher.choose_agent_with_lion_agent(task, [plain_agent], coordinator=None))

    assert decision.agent is None
    assert decision.dispatch_status == "no_candidate"


def test_digital_employee_scope_allows_role_backed_agent():
    dispatcher = _load_dispatcher()
    selected = _agent("ops-1", role_key="monitor")
    task = SimpleNamespace(
        id="task-1",
        tenant_id="tenant-1",
        title="巡检",
        description="检查磁盘",
        source="workflow",
        priority=3,
        status="pending_dispatch",
        progress=0,
        required_capabilities=[],
        payload={"_dispatch_scope": "digital_employee"},
        requested_agent_id="ops-1",
        room_id=None,
        creator_id=None,
    )

    decision = asyncio.run(dispatcher.choose_agent_with_lion_agent(task, [selected], coordinator=None))

    assert decision.agent is selected
    assert decision.dispatch_status == "user_specified"
