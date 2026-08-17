# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import uuid
import pytest

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


def test_execute_prompt_sends_unique_execution_ids():
    adapter, client = _load_adapter()

    for _ in range(2):
        asyncio.run(
            adapter.execute_prompt(
                workspace_id="employee-1",
                session_id="run-1",
                message="execute task",
            )
        )

    execution_ids = [payload["execution_id"] for payload in client.payloads[-2:]]
    assert len(set(execution_ids)) == 2
    assert all(uuid.UUID(value).hex == value for value in execution_ids)


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


def test_stream_prompt_events_sends_unique_execution_ids():
    adapter, client = _load_adapter()

    async def consume():
        for _ in range(2):
            async for _event in adapter.stream_prompt_events(
                workspace_id="employee-1",
                session_id="run-1",
                message="execute task",
            ):
                pass

    asyncio.run(consume())

    execution_ids = [payload["execution_id"] for payload in client.payloads[-2:]]
    assert len(set(execution_ids)) == 2
    assert all(uuid.UUID(value).hex == value for value in execution_ids)


def test_execute_prompt_sends_llm_payload():
    """ResolvedLlm 必须序列化进请求体的 llm 键，结构与 lion_agent 契约一致。"""
    from types import SimpleNamespace

    adapter, client = _load_adapter()

    # 夹具必须同时提供 model_name：生产代码除了 to_payload() 还要读它来写顶层 model。
    llm = SimpleNamespace(
        model_name="aioe",
        to_payload=lambda: {"base_url": "https://x/v1", "api_key": "sk-1", "model": "aioe"},
    )
    asyncio.run(adapter.execute_prompt(
        workspace_id="t1:warroom", session_id="s1", message="hi", llm=llm,
    ))

    assert client.payloads[-1]["llm"] == {"base_url": "https://x/v1", "api_key": "sk-1", "model": "aioe"}
    # 顶层 model 才是 lion_agent 实际读取模型名的通道，必须一并断言
    assert client.payloads[-1]["model"] == "aioe"


def test_execute_prompt_omits_llm_key_when_absent():
    """不传 llm 时请求体不应出现该键，保持对旧调用方的兼容。"""
    adapter, client = _load_adapter()

    asyncio.run(adapter.execute_prompt(workspace_id="t1:warroom", session_id="s1", message="hi"))

    assert "llm" not in client.payloads[-1]


def test_execute_prompt_carries_memory_section():
    """Cognee 下线后只剩 memory 一个记忆参数位。"""
    adapter, client = _load_adapter()

    asyncio.run(
        adapter.execute_prompt(
            workspace_id="ws-1",
            session_id="s-1",
            message="hi",
            memory={"enabled": True, "employee_id": "employee-1"},
        )
    )

    assert client.payloads[-1]["memory"] == {"enabled": True, "employee_id": "employee-1"}
    assert "cognee" not in client.payloads[-1]


def test_execute_prompt_rejects_cognee_keyword():
    """cognee 参数位已移除；再传就该是 TypeError，而不是被静默忽略。

    静默忽略会让漏改的调用方以为记忆段发出去了。
    """
    adapter, _ = _load_adapter()

    with pytest.raises(TypeError):
        asyncio.run(
            adapter.execute_prompt(
                workspace_id="ws-1",
                session_id="s-1",
                message="hi",
                cognee={"endpoint": "http://lion-memory"},
            )
        )


def test_stream_prompt_events_carries_memory():
    """流式路径与非流式必须同形状——execute_coordinator 走的是这一条。"""
    adapter, client = _load_adapter()

    async def _drain():
        events = []
        async for item in adapter.stream_prompt_events(
            workspace_id="ws-1",
            session_id="s-1",
            message="hi",
            memory={"enabled": True, "mode": "both", "top_k": 5},
        ):
            events.append(item)
        return events

    asyncio.run(_drain())

    assert client.payloads[-1]["memory"] == {"enabled": True, "mode": "both", "top_k": 5}
    assert "cognee" not in client.payloads[-1]


def test_adapter_never_names_memory_workspace_id():
    """契约 §4.3：全文禁止 memory.workspace_id。adapter 不得替 payload 补这个键。"""
    adapter, client = _load_adapter()

    asyncio.run(
        adapter.execute_prompt(
            workspace_id="ws-continuation",
            session_id="s-1",
            message="hi",
            memory={"enabled": True, "employee_id": "employee-1"},
        )
    )

    assert "workspace_id" not in client.payloads[-1]["memory"]
    assert client.payloads[-1]["workspace_id"] == "ws-continuation"


def test_identity_only_memory_disables_middleware_but_keeps_tenant_id():
    """D36 / D40：非员工链路不需长期记忆，但仍必须表明租户身份。

    enabled=False 只决定「不挂载 middleware」，**不决定「不校验身份」**：
    D40 已裁决归属校验不受 enabled 门控、且在 enabled 判断之前执行。
    """
    adapter, _ = _load_adapter()

    memory = adapter.identity_only_memory("tenant-1")

    assert memory == {
        "enabled": False,
        "tenant_id": "tenant-1",
        "employee_id": None,
    }
    # 确保 identity_only_memory 不发 provenance / mode / top_k：它们只为员工有意义
    assert set(memory) == {"enabled", "tenant_id", "employee_id"}


def test_every_execute_prompt_call_site_passes_tenant_identity():
    """D36 的守护网：非员工链路也必须带租户身份，5 个调用点一个都不能漏。

    internal token 是当前唯一的横向边界——不带身份的 execute 意味着
    「持 token 者可在任意 workspace 执行」。P4 会在 1B 把
    LION_AGENT_REQUIRE_WORKSPACE_IDENTITY 翻转为 true，门槛是
    owner_identity_missing 计数归零，而归零的前提就是这 5 处全部补齐。

    用 AST 静态检查：grep 会被换行与格式化骗过去；新增调用点时
    这条测试会自动失败，强制实施者回来补参数。
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sources = (
        "services/inspection/logic.py",
        "services/agent/report_render.py",
        "services/agent/views.py",
        "services/command_center/dispatcher.py",
    )

    call_sites = []
    for rel in sources:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee_name(node.func) == "execute_prompt":
                call_sites.append((rel, node.lineno, {kw.arg for kw in node.keywords}))

    # 基线：D36 列了 4 处，实测 dispatcher.py 有两处（:292 首次 + :304 重试）
    assert len(call_sites) == 5, f"execute_prompt 调用点数量变了，请更新本测试: {call_sites}"

    missing = [(rel, lineno) for rel, lineno, kwargs in call_sites if "memory" not in kwargs]
    assert missing == [], f"这些 execute_prompt 调用点没有传 memory 身份段: {missing}"


def _callee_name(func) -> str | None:
    """取被调用者的名字，同时兼容 `execute_prompt(...)` 与 `mod.execute_prompt(...)`。"""
    import ast

    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None
