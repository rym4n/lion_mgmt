# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path
import sys
import types

import pytest


STUB_MODULES = [
    "services",
    "services.digital_employee",
    "services.digital_employee.lion_agent_adapter",
    "services.digital_employee.lion_agent_client",
]
_MISSING_MODULE = object()


@pytest.fixture(autouse=True)
def _restore_stubbed_modules():
    original_modules = {
        name: sys.modules.get(name, _MISSING_MODULE) for name in STUB_MODULES
    }
    yield
    for name, original in original_modules.items():
        if original is _MISSING_MODULE:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


def _load_adapter():
    for name in STUB_MODULES:
        sys.modules.pop(name, None)

    services_module = types.ModuleType("services")
    services_module.__path__ = []
    sys.modules["services"] = services_module
    digital_employee_module = types.ModuleType("services.digital_employee")
    digital_employee_module.__path__ = []
    sys.modules["services.digital_employee"] = digital_employee_module

    client_module = types.ModuleType("services.digital_employee.lion_agent_client")

    class LionAgentClient:
        payloads = []

        async def execute(self, payload):
            self.payloads.append(payload)
            return {"content": "ok"}

        async def stream_execute(self, payload):
            self.payloads.append(payload)
            if False:
                yield ""

    client_module.LionAgentClient = LionAgentClient
    sys.modules["services.digital_employee.lion_agent_client"] = client_module

    path = (
        Path(__file__).resolve().parents[2]
        / "services/digital_employee/lion_agent_adapter.py"
    )
    spec = importlib.util.spec_from_file_location(
        "services.digital_employee.lion_agent_adapter", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["services.digital_employee.lion_agent_adapter"] = module
    spec.loader.exec_module(module)
    return module, LionAgentClient


def test_execute_prompt_sends_model_override():
    adapter, client = _load_adapter()

    result = asyncio.run(
        adapter.execute_prompt(
            workspace_id="tenant-1:command-center",
            session_id="dispatch-1",
            request_id="task-1",
            message="route task",
            model="aioe-lite",
            system_prompt="immutable coordinator rules",
        )
    )

    assert result == "ok"
    assert client.payloads[-1]["model"] == "aioe-lite"
    assert client.payloads[-1]["system_prompt"] == "immutable coordinator rules"


def test_execute_prompt_omits_model_without_override():
    adapter, client = _load_adapter()

    asyncio.run(
        adapter.execute_prompt(
            workspace_id="employee-1",
            session_id="run-1",
            message="execute task",
        )
    )

    assert "model" not in client.payloads[-1]
    assert "system_prompt" not in client.payloads[-1]


def test_stream_prompt_events_sends_model_override():
    adapter, client = _load_adapter()

    async def consume():
        return [
            event
            async for event in adapter.stream_prompt_events(
                workspace_id="tenant-1:command-center",
                session_id="dispatch-1",
                message="route task",
                model="aioe-lite",
                system_prompt="stream role override",
            )
        ]

    assert asyncio.run(consume()) == []
    assert client.payloads[-1]["model"] == "aioe-lite"
    assert client.payloads[-1]["system_prompt"] == "stream role override"


def test_stream_prompt_events_omits_model_without_override():
    adapter, client = _load_adapter()

    async def consume():
        return [
            event
            async for event in adapter.stream_prompt_events(
                workspace_id="employee-1",
                session_id="run-1",
                message="execute task",
            )
        ]

    assert asyncio.run(consume()) == []
    assert "model" not in client.payloads[-1]


def test_execute_prompt_sends_llm_payload(monkeypatch):
    """ResolvedLlm 必须序列化进请求体的 llm 键，结构与 lion_agent 契约一致。"""
    import asyncio
    from types import SimpleNamespace

    from services.digital_employee import lion_agent_adapter as adapter

    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def execute(self, payload):
            captured.update(payload)
            return {"content": "ok"}

    monkeypatch.setattr(adapter, "LionAgentClient", FakeClient)

    # 夹具必须同时提供 model_name：生产代码除了 to_payload() 还要读它来写顶层 model。
    llm = SimpleNamespace(
        model_name="aioe",
        to_payload=lambda: {"base_url": "https://x/v1", "api_key": "sk-1", "model": "aioe"},
    )
    asyncio.run(adapter.execute_prompt(
        workspace_id="t1:warroom", session_id="s1", message="hi", llm=llm,
    ))

    assert captured["llm"] == {"base_url": "https://x/v1", "api_key": "sk-1", "model": "aioe"}
    # 顶层 model 才是 lion_agent 实际读取模型名的通道，必须一并断言
    assert captured["model"] == "aioe"


def test_execute_prompt_omits_llm_key_when_absent(monkeypatch):
    """不传 llm 时请求体不应出现该键，保持对旧调用方的兼容。"""
    import asyncio

    from services.digital_employee import lion_agent_adapter as adapter

    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def execute(self, payload):
            captured.update(payload)
            return {"content": "ok"}

    monkeypatch.setattr(adapter, "LionAgentClient", FakeClient)
    asyncio.run(adapter.execute_prompt(workspace_id="t1:warroom", session_id="s1", message="hi"))

    assert "llm" not in captured
