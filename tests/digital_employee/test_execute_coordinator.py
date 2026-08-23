# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types

import orjson
import pytest


STUB_MODULES = [
    "models",
    "models.command_center",
    "settings",
    "aiocache",
    "sanic",
    "sanic.log",
    "services",
    "services.command_center",
    "services.command_center.manager",
    "services.digital_employee",
    "services.digital_employee.manager",
    "services.digital_employee.aioe_credential_manager",
    "services.digital_employee.prompt_builder",
    "services.digital_employee.lion_agent_adapter",
    "services.digital_employee.lion_agent_client",
    "services.digital_employee.execute_coordinator",
    "services.llm_config",
    "services.llm_config.resolver",
    "libs.error_code.exception",
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


def _load_module(name: str, relative_path: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _install_stubs(progress_calls: list[tuple[dict, str | None]]):
    sync_calls = []
    workspace_sync_calls = []

    class CommandAgent:
        pass

    class CommandTask:
        pass

    async def update_task_progress(task, data, operator_id=None):
        progress_calls.append((data, operator_id))
        if "status" in data:
            task.status = data["status"]
        if "progress" in data:
            task.progress = data["progress"]
        return task

    sys.modules.setdefault("models", types.ModuleType("models"))
    command_center_module = types.ModuleType("models.command_center")
    command_center_module.CommandAgent = CommandAgent
    command_center_module.CommandTask = CommandTask
    sys.modules["models.command_center"] = command_center_module

    settings_module = types.ModuleType("settings")
    settings_module.AIOE_INTERNAL_URL = "http://lion-mgmt"
    settings_module.WR_TOPIC = "wr-topic"
    sys.modules["settings"] = settings_module

    aiocache_module = types.ModuleType("aiocache")
    aiocache_module.caches = SimpleNamespace(get=lambda name: SimpleNamespace(client=None))
    sys.modules["aiocache"] = aiocache_module

    sanic_module = types.ModuleType("sanic")
    sanic_log_module = types.ModuleType("sanic.log")
    sanic_log_module.logger = SimpleNamespace(warning=lambda *args, **kwargs: None)
    sys.modules["sanic"] = sanic_module
    sys.modules["sanic.log"] = sanic_log_module

    services_module = types.ModuleType("services")
    services_module.__path__ = []
    sys.modules["services"] = services_module

    command_center_pkg = types.ModuleType("services.command_center")
    command_center_pkg.__path__ = []
    sys.modules["services.command_center"] = command_center_pkg
    command_center_manager = types.ModuleType("services.command_center.manager")
    command_center_manager.update_task_progress = update_task_progress
    sys.modules["services.command_center.manager"] = command_center_manager

    digital_employee_pkg = types.ModuleType("services.digital_employee")
    digital_employee_pkg.__path__ = []
    sys.modules["services.digital_employee"] = digital_employee_pkg

    digital_employee_manager = types.ModuleType("services.digital_employee.manager")

    async def sync_employee_workspace(employee, force=False):
        sync_calls.append({"employee_id": employee.id, "force": force})
        employee.workspace_synced = True
        return employee

    digital_employee_manager.sync_employee_workspace = sync_employee_workspace
    digital_employee_manager.sync_calls = sync_calls
    sys.modules["services.digital_employee.manager"] = digital_employee_manager

    aioe_credential_manager = types.ModuleType("services.digital_employee.aioe_credential_manager")
    aioe_credential_manager.get_employee_api_token = lambda employee: _async_value(f"api-token-{employee.id}")
    sys.modules["services.digital_employee.aioe_credential_manager"] = aioe_credential_manager

    prompt_builder = types.ModuleType("services.digital_employee.prompt_builder")
    prompt_builder.build_runtime_prompt = lambda task, employee: f"prompt:{task.id}:{employee.id}"
    sys.modules["services.digital_employee.prompt_builder"] = prompt_builder

    adapter = types.ModuleType("services.digital_employee.lion_agent_adapter")
    adapter.stream_prompt_events = _stream_prompt_events
    sys.modules["services.digital_employee.lion_agent_adapter"] = adapter

    lion_agent_client = types.ModuleType("services.digital_employee.lion_agent_client")

    class LionAgentClient:
        async def sync_workspace(self, workspace_id, files, role_version=None,
                                 skill_versions=None, tenant_id=None, employee_id=None):
            workspace_sync_calls.append(
                {
                    "workspace_id": workspace_id,
                    "files": files,
                    "role_version": role_version,
                    "skill_versions": skill_versions or {},
                    "tenant_id": tenant_id,
                    "employee_id": employee_id,
                }
            )
            return {"workspace_id": workspace_id, "etag": "readonly-etag", "file_count": len(files)}

    lion_agent_client.LionAgentClient = LionAgentClient
    lion_agent_client.workspace_sync_calls = workspace_sync_calls
    sys.modules["services.digital_employee.lion_agent_client"] = lion_agent_client

    llm_config_pkg = types.ModuleType("services.llm_config")
    llm_config_pkg.__path__ = []
    sys.modules["services.llm_config"] = llm_config_pkg
    error_module = types.ModuleType("libs.error_code.exception")

    class CustomError(Exception):
        pass

    error_module.CustomError = CustomError
    sys.modules["libs.error_code.exception"] = error_module

    resolver = types.ModuleType("services.llm_config.resolver")

    async def resolve_llm(tenant_id, **kwargs):
        if kwargs.get("purpose") == "embedding":
            raise CustomError("LLM_CONFIG_NOT_CONFIGURED")
        return SimpleNamespace(config_id=kwargs.get("config_id"), model_name="test-model")

    resolver.resolve_llm = resolve_llm
    sys.modules["services.llm_config.resolver"] = resolver


async def _async_value(value):
    return value


RECORDED_STREAM_PAYLOADS = []


async def _stream_prompt_events(**payload):
    RECORDED_STREAM_PAYLOADS.append(payload)
    yield "thinking", {"content": "分析中"}
    yield "tool_call", {"data": {"name": "check_disk"}, "status": "started"}
    yield "delta", {"content": "A"}
    yield "delta", {"delta": "B"}
    yield "end", {
        "content": "AB",
        "usage": {
            "fallback": False,
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "model_calls": 1,
        },
    }


async def _approval_required_stream(**payload):
    RECORDED_STREAM_PAYLOADS.append(payload)
    yield "approval_required", {"tool": "write_file", "risk_level": "high"}
    yield "end", {"content": "should-not-complete"}


async def _tool_call_name_stream(**payload):
    RECORDED_STREAM_PAYLOADS.append(payload)
    yield "tool_call", {"data": {"tool_call_name": "aioe_cli"}, "status": "started"}
    yield "tool_call", {"data": {"tool_call_name": "aioe_cli"}, "status": "completed"}
    yield "end", {"content": "done"}


async def _runtime_degraded_stream(**payload):
    RECORDED_STREAM_PAYLOADS.append(payload)
    yield "runtime_degraded", {
        "code": "context_compression_degraded",
        "message": "上下文压缩失败，已保留最近上下文继续执行",
        "blocking": False,
    }
    yield "error", {
        "code": "memory_write_degraded",
        "message": "记忆写回降级",
        "blocking": False,
    }
    yield "end", {"content": "done"}


class FakeRedis:
    def __init__(self):
        self.messages = []

    async def publish(self, channel, message):
        self.messages.append((channel, orjson.loads(message)))


class FakeTask(SimpleNamespace):
    async def save(self, update_fields=None):
        self.saved_update_fields.append(update_fields)


def test_run_closes_lifecycle_and_streams_without_duplicate_end_content():
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    redis = FakeRedis()
    execute_coordinator.caches = SimpleNamespace(get=lambda name: SimpleNamespace(client=redis))

    task = FakeTask(
        id="task-1",
        tenant_id="tenant-1",
        run_id=None,
        room_id="room-1",
        creator_id="creator-1",
        source="manual",
        progress=0,
        status="pending",
        title="巡检",
        description="检查磁盘",
        payload={},
        result=None,
        saved_update_fields=[],
    )
    employee = SimpleNamespace(
        id="employee-1",
        role_key="ops",
        agent_key="ops-1",
        name="运维员工",
        llm_config_id="llm-config-1",
    )

    result = asyncio.run(execute_coordinator.run(task, employee, operator_id="operator-1"))

    assert result["content"] == "AB"
    assert result["usage"]["total_tokens"] == 120
    assert result["usage"]["model_calls"] == 1
    assert task.result == "AB"
    assert task.saved_update_fields == [["result", "utime"]]
    assert [call[0]["status"] for call in progress_calls] == ["running", "completed"]
    assert [call[1] for call in progress_calls] == ["operator-1", "operator-1"]
    assert sys.modules["services.digital_employee.manager"].sync_calls == [{"employee_id": "employee-1", "force": True}]

    payload = RECORDED_STREAM_PAYLOADS[0]
    assert payload["workspace_id"] == "employee-1"
    assert payload["session_id"] == "task-1"
    assert payload["message"] == "prompt:task-1:employee-1"
    assert payload["llm"].config_id == "llm-config-1"
    assert "model" not in payload
    assert payload["memory"] == {
        "enabled": True,
        "mode": "both",
        "top_k": 5,
        "tenant_id": "tenant-1",
        "employee_id": "employee-1",
        "provenance": {
            "role_key": "ops",
            "task_id": "task-1",
            "request_id": "task-1",
            "source_kind": "manual",
        },
        "embedding": None,
    }
    assert payload["aioe"] == {
        "base_url": "http://lion-mgmt",
        "token": "api-token-employee-1",
        "tenant_id": "tenant-1",
        "agent_run_id": None,
    }

    published = [message for _, message in redis.messages]
    assert [message["type"] for message in published] == [
        "ai-thinking",
        "ai-thinking",
        "ai-reply-delta",
        "ai-reply-delta",
        "ai-reply-end",
    ]
    assert [message.get("content") for message in published if message["type"] == "ai-reply-delta"] == ["A", "B"]
    assert all(message["stream_id"] == "digital-employee:task-1" for message in published)


def test_run_uses_command_run_session_and_emits_runtime_callbacks():
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    redis = FakeRedis()
    execute_coordinator.caches = SimpleNamespace(get=lambda name: SimpleNamespace(client=redis))

    task = FakeTask(
        id="task-1",
        tenant_id="tenant-1",
        run_id=None,
        room_id=None,
        creator_id="creator-1",
        source="manual",
        progress=0,
        status="pending",
        title="巡检",
        description="检查磁盘",
        payload={},
        result=None,
        saved_update_fields=[],
    )
    employee = SimpleNamespace(id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工")
    command_run = SimpleNamespace(run_id="run-1")
    runtime_events = []

    async def on_runtime_event(event_type, event):
        runtime_events.append((event_type, event))

    result = asyncio.run(
        execute_coordinator.run(
            task,
            employee,
            operator_id="operator-1",
            command_run=command_run,
            on_runtime_event=on_runtime_event,
        )
    )

    assert result["content"] == "AB"
    assert result["usage"]["input_tokens"] == 100
    assert RECORDED_STREAM_PAYLOADS[0]["session_id"] == "run-1"
    assert [event_type for event_type, _ in runtime_events] == ["thinking", "tool_call", "delta", "delta", "end"]
    assert task.result is None
    assert task.saved_update_fields == []
    assert [call[0]["status"] for call in progress_calls] == ["running"]
    assert sys.modules["services.digital_employee.manager"].sync_calls == [{"employee_id": "employee-1", "force": True}]


def test_run_syncs_and_uses_continuation_read_only_workspace():
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )

    task = FakeTask(
        id="task-1",
        tenant_id="tenant-1",
        run_id=None,
        room_id=None,
        creator_id="creator-1",
        source="manual",
        progress=0,
        status="pending",
        title="巡检",
        description="检查磁盘",
        payload={},
        result=None,
        saved_update_fields=[],
    )
    employee = SimpleNamespace(id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工")
    command_run = SimpleNamespace(run_id="run-2")
    continuation_workspace = {
        "workspace_id": "employee-1:approval-approval-1:read-only",
        "files": {"system_prompt.md": "role", "policy.json": {"continuation_mutation": {"mode": "read_only"}}},
        "role_version": 3,
        "skill_versions": {"write_file": 1},
    }

    result = asyncio.run(
        execute_coordinator.run(
            task,
            employee,
            operator_id="approver-1",
            command_run=command_run,
            continuation_workspace=continuation_workspace,
        )
    )

    assert result["content"] == "AB"
    assert result["usage"]["total_tokens"] == 120
    assert RECORDED_STREAM_PAYLOADS[0]["workspace_id"] == "employee-1:approval-approval-1:read-only"
    assert RECORDED_STREAM_PAYLOADS[0]["session_id"] == "run-2"
    workspace_sync_calls = sys.modules["services.digital_employee.lion_agent_client"].workspace_sync_calls
    assert workspace_sync_calls == [
        {
            "workspace_id": "employee-1:approval-approval-1:read-only",
            "files": {"system_prompt.md": "role", "policy.json": {"continuation_mutation": {"mode": "read_only"}}},
            "role_version": 3,
            "skill_versions": {"write_file": 1},
            "tenant_id": "tenant-1",
            "employee_id": "employee-1",
        }
    ]
    assert sys.modules["services.digital_employee.manager"].sync_calls == [{"employee_id": "employee-1", "force": True}]


def test_run_returns_control_event_without_marking_task_completed():
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()
    sys.modules["services.digital_employee.lion_agent_adapter"].stream_prompt_events = _approval_required_stream
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    execute_coordinator.stream_prompt_events = _approval_required_stream
    redis = FakeRedis()
    execute_coordinator.caches = SimpleNamespace(get=lambda name: SimpleNamespace(client=redis))

    task = FakeTask(
        id="task-1",
        tenant_id="tenant-1",
        run_id=None,
        room_id=None,
        creator_id="creator-1",
        source="manual",
        progress=0,
        status="pending",
        title="巡检",
        description="检查磁盘",
        payload={},
        result=None,
        saved_update_fields=[],
    )
    employee = SimpleNamespace(id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工")
    command_run = SimpleNamespace(run_id="run-1")

    async def on_runtime_event(event_type, event):
        return {"event_type": event_type, "message": "数字员工请求人工审批"}

    result = asyncio.run(
        execute_coordinator.run(
            task,
            employee,
            operator_id="operator-1",
            command_run=command_run,
            on_runtime_event=on_runtime_event,
        )
    )

    assert result["policy"] == "approval_required"
    assert result["run_id"] == "run-1"
    assert result["usage"]["total_tokens"] == 0
    assert task.result is None
    assert task.saved_update_fields == []
    assert [call[0]["status"] for call in progress_calls] == ["running"]


def test_run_room_tool_message_uses_agentscope_tool_call_name():
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()
    sys.modules["services.digital_employee.lion_agent_adapter"].stream_prompt_events = _tool_call_name_stream
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    execute_coordinator.stream_prompt_events = _tool_call_name_stream
    redis = FakeRedis()
    execute_coordinator.caches = SimpleNamespace(get=lambda name: SimpleNamespace(client=redis))

    task = FakeTask(
        id="task-1",
        tenant_id="tenant-1",
        run_id=None,
        room_id="room-1",
        creator_id="creator-1",
        source="manual",
        progress=0,
        status="pending",
        title="巡检",
        description="检查磁盘",
        payload={},
        result=None,
        saved_update_fields=[],
    )
    employee = SimpleNamespace(id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工")

    result = asyncio.run(execute_coordinator.run(task, employee, operator_id="operator-1"))

    assert result["content"] == "done"
    published = [message for _, message in redis.messages]
    tool_messages = [message for message in published if message["type"] == "ai-thinking"]
    assert [message["tool_name"] for message in tool_messages] == ["aioe_cli", "aioe_cli"]
    assert [message["content"] for message in tool_messages] == ["正在执行 aioe_cli", "aioe_cli 执行完成"]


def test_non_blocking_runtime_events_do_not_abort_command_run():
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    execute_coordinator.stream_prompt_events = _runtime_degraded_stream
    task = FakeTask(
        id="task-1", tenant_id="tenant-1", run_id=None, room_id=None,
        creator_id="creator-1", source="manual", progress=0, status="pending",
        title="巡检", description="检查磁盘", payload={}, result=None,
        saved_update_fields=[],
    )
    employee = SimpleNamespace(id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工")
    runtime_events = []

    async def on_runtime_event(event_type, event):
        runtime_events.append((event_type, event))

    result = asyncio.run(execute_coordinator.run(
        task,
        employee,
        command_run=SimpleNamespace(run_id="run-1"),
        on_runtime_event=on_runtime_event,
    ))

    assert result["content"] == "done"
    assert [event_type for event_type, _ in runtime_events] == [
        "runtime_degraded", "error", "end",
    ]


def test_memory_owner_is_employee_not_continuation_workspace():
    """SPEC §5.5：continuation 执行的记忆必须仍归员工本体。

    这是本次迁移最容易照搬错的一处——顶层 workspace_id 已切到临时 workspace，
    但 memory.employee_id 必须岿然不动。
    """
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    redis = FakeRedis()
    execute_coordinator.caches = SimpleNamespace(get=lambda name: SimpleNamespace(client=redis))

    task = FakeTask(
        id="task-1",
        tenant_id="tenant-1",
        run_id=None,
        room_id=None,
        creator_id="creator-1",
        source="manual",
        progress=0,
        status="pending",
        title="巡检",
        description="检查磁盘",
        payload={},
        result=None,
        saved_update_fields=[],
    )
    employee = SimpleNamespace(id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工")
    continuation_workspace = {
        "workspace_id": "employee-1:approval-abc123def456:read-only",
        "files": {"system_prompt.md": "role"},
        "role_version": 3,
        "skill_versions": {},
    }

    asyncio.run(
        execute_coordinator.run(
            task,
            employee,
            operator_id="approver-1",
            command_run=SimpleNamespace(run_id="run-2"),
            continuation_workspace=continuation_workspace,
        )
    )

    payload = RECORDED_STREAM_PAYLOADS[0]
    # 顶层执行 workspace 确实切走了
    assert payload["workspace_id"] == "employee-1:approval-abc123def456:read-only"
    # 记忆 owner 仍是员工本体，两者不相等
    assert payload["memory"]["employee_id"] == "employee-1"
    assert payload["memory"]["employee_id"] != payload["workspace_id"]
    # 契约 §4.3：memory 段禁止出现 workspace_id
    assert "workspace_id" not in payload["memory"]
    assert "dataset_name" not in payload["memory"]


def test_memory_mode_and_top_k_are_fixed_constants():
    """契约 D2：lion_mgmt 恒发 both/5，不从员工或任务读取。

    员工对象上即使存在同名属性也不得影响 payload——否则 lion_agent 侧的
    MemoryResource identity 会被意外扰动并触发重建。
    """
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    redis = FakeRedis()
    execute_coordinator.caches = SimpleNamespace(get=lambda name: SimpleNamespace(client=redis))

    task = FakeTask(
        id="task-9",
        tenant_id="tenant-9",
        run_id=None,
        room_id=None,
        creator_id="creator-9",
        source="timed",
        progress=0,
        status="pending",
        title="t",
        description="d",
        payload={},
        result=None,
        saved_update_fields=[],
        mode="agent_control",
        top_k=99,
    )
    employee = SimpleNamespace(
        id="employee-9", role_key="monitor", agent_key="mon-1", name="监控",
        mode="static_control", top_k=42,
    )

    asyncio.run(execute_coordinator.run(task, employee))

    memory = RECORDED_STREAM_PAYLOADS[0]["memory"]
    assert memory["mode"] == "both"
    assert memory["top_k"] == 5
    assert memory["provenance"]["source_kind"] == "timed"
    assert memory["provenance"]["role_key"] == "monitor"


def test_memory_payload_key_set_is_exactly_the_contract():
    """契约 §3 的形状是逐字固定的：多一个键或少一个键都要在这里失败。"""
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    redis = FakeRedis()
    execute_coordinator.caches = SimpleNamespace(get=lambda name: SimpleNamespace(client=redis))

    task = FakeTask(
        id="task-2", tenant_id="tenant-2", run_id=None, room_id=None,
        creator_id="c", source="event", progress=0, status="pending",
        title="t", description="d", payload={}, result=None, saved_update_fields=[],
    )
    employee = SimpleNamespace(id="employee-2", role_key="net", agent_key="net-1", name="网络")

    asyncio.run(execute_coordinator.run(task, employee))

    memory = RECORDED_STREAM_PAYLOADS[0]["memory"]
    assert set(memory) == {
        "enabled", "mode", "top_k", "tenant_id", "employee_id", "provenance", "embedding",
    }
    assert set(memory["provenance"]) == {"role_key", "task_id", "request_id", "source_kind"}
    # Cognee 的三个远程凭证字段必须已经消失
    assert not {"endpoint", "jwt", "dataset_name"} & set(memory)


def _run_once(*, continuation_workspace=None):
    """跑一次 run() 并取回发给 lion_agent 的 payload。

    Cognee 后端已整体下线（SPEC §8 第 9 步），不再有 backend 参数——
    payload 恒带 memory 段。
    """
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    execute_coordinator.caches = SimpleNamespace(get=lambda name: SimpleNamespace(client=FakeRedis()))

    task = FakeTask(
        id="task-1", tenant_id="tenant-1", run_id=None, room_id=None,
        creator_id="creator-1", source="manual", progress=0, status="pending",
        title="巡检", description="检查磁盘", payload={}, result=None,
        saved_update_fields=[],
    )
    employee = SimpleNamespace(id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工")
    asyncio.run(
        execute_coordinator.run(
            task, employee, continuation_workspace=continuation_workspace
        )
    )
    return RECORDED_STREAM_PAYLOADS[0]


def test_payload_carries_no_cognee_section_at_all():
    """Cognee 下线后 payload 里不得再出现 cognee 键。

    不是「值为 None」而是「键不存在」——留一个恒 None 的键会让人以为
    回滚路径还在，而 lion_agent 侧的 ExecuteRequest 已经不认它了。
    """
    payload = _run_once()

    assert "cognee" not in payload
    assert payload["memory"]["employee_id"] == "employee-1"


def test_payload_always_carries_memory_section():
    """没有后端分支了，memory 段必须恒非空。"""
    payload = _run_once()

    assert payload["memory"] is not None
    assert payload["memory"]["enabled"] is True


def test_continuation_payload_keeps_employee_as_memory_owner():
    """continuation workspace 是派生的只读工作区，
    记忆归属仍是员工本体，不随 workspace_id 改变。"""
    payload = _run_once(
        continuation_workspace={
            "workspace_id": "employee-1:approval-abc123def456:read-only",
            "files": {"system_prompt.md": "role"},
            "role_version": 3,
            "skill_versions": {},
        },
    )

    assert payload["workspace_id"] == "employee-1:approval-abc123def456:read-only"
    assert payload["memory"]["employee_id"] == "employee-1"


def test_payload_top_level_key_set_is_the_contract():
    """顶层键集合固定；cognee 不在其中。"""
    payload = _run_once()

    assert set(payload) >= {
        "workspace_id", "session_id", "request_id", "message",
        "memory", "aioe", "llm",
    }
    assert "cognee" not in payload


def test_reme_payload_matches_contract_schema_exactly():
    """契约 §3 的 memory 段形状——P4 在 lion_agent 侧写对偶断言。
    两条测试同时绿，才说明两侧对齐了同一个形状。"""
    payload = _run_once()
    memory = payload["memory"]

    assert memory["enabled"] is True
    assert memory["mode"] == "both"
    assert memory["top_k"] == 5
    assert isinstance(memory["tenant_id"], str) and memory["tenant_id"]
    assert isinstance(memory["employee_id"], str) and memory["employee_id"]
    assert set(memory["provenance"]) == {"role_key", "task_id", "request_id", "source_kind"}
    # D45：dimensions 为 None 时不输出该键，故三键与四键都合法
    assert memory["embedding"] is None or set(memory["embedding"]) in (
        {"base_url", "api_key", "model"},
        {"base_url", "api_key", "model", "dimensions"},
    )


def test_run_resolves_embedding_and_forwards_four_key_payload():
    """1B：embedding 配置存在时必须真实进入 memory payload。"""
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()

    class Embedding:
        config_id = "embedding-config-1"

        def to_payload(self):
            return {
                "base_url": "https://embedding.example/v1",
                "api_key": "sk-live-embedding-secret",
                "model": "qwen3-embedding-4b",
                "dimensions": 1024,
            }

    original = sys.modules["services.llm_config.resolver"].resolve_llm

    async def resolve_llm(tenant_id, **kwargs):
        if kwargs.get("purpose") == "embedding":
            return Embedding()
        return await original(tenant_id, **kwargs)

    sys.modules["services.llm_config.resolver"].resolve_llm = resolve_llm
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    execute_coordinator.caches = SimpleNamespace(
        get=lambda name: SimpleNamespace(client=FakeRedis())
    )
    task = FakeTask(
        id="task-embedding", tenant_id="tenant-1", run_id=None, room_id=None,
        creator_id="c", source="manual", progress=0, status="pending",
        title="t", description="d", payload={}, result=None, saved_update_fields=[],
    )
    employee = SimpleNamespace(
        id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工"
    )

    asyncio.run(execute_coordinator.run(task, employee))

    assert RECORDED_STREAM_PAYLOADS[0]["memory"]["embedding"] == {
        "base_url": "https://embedding.example/v1",
        "api_key": "sk-live-embedding-secret",
        "model": "qwen3-embedding-4b",
        "dimensions": 1024,
    }


def test_run_only_downgrades_custom_error_from_embedding_resolution():
    """未配置 embedding 可降级；数据库/代码故障不得被静默吞掉。"""
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()

    async def resolve_llm(tenant_id, **kwargs):
        if kwargs.get("purpose") == "embedding":
            raise RuntimeError("database unavailable")
        return SimpleNamespace(config_id=None, model_name="test-model")

    sys.modules["services.llm_config.resolver"].resolve_llm = resolve_llm
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    task = FakeTask(
        id="task-error", tenant_id="tenant-1", run_id=None, room_id=None,
        creator_id="c", source="manual", progress=0, status="pending",
        title="t", description="d", payload={}, result=None, saved_update_fields=[],
    )
    employee = SimpleNamespace(
        id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工"
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(execute_coordinator.run(task, employee))


def test_memory_embedding_is_resolved_from_tenant_config():
    """SPEC §5.6：embedding 走 resolve_llm(tenant_id, purpose='embedding')。"""
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()

    class _Embedding:
        def to_payload(self):
            return {
                "base_url": "http://embed",
                "api_key": "sk-embed",
                "model": "text-embedding-v4",
                "dimensions": 1024,
            }

    class _Chat:
        config_id = "llm-config-1"
        model_name = "test-model"

    async def resolve_llm(tenant_id, **kwargs):
        if kwargs.get("purpose") == "embedding":
            return _Embedding()
        return _Chat()

    sys.modules["services.llm_config.resolver"].resolve_llm = resolve_llm
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    task = FakeTask(
        id="task-embed", tenant_id="tenant-1", run_id=None, room_id=None,
        creator_id="c", source="manual", progress=0, status="pending",
        title="t", description="d", payload={}, result=None, saved_update_fields=[],
    )
    employee = SimpleNamespace(
        id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工"
    )

    asyncio.run(execute_coordinator.run(task, employee))

    assert len(RECORDED_STREAM_PAYLOADS) == 1
    memory = RECORDED_STREAM_PAYLOADS[0]["memory"]
    assert memory["embedding"] == {
        "base_url": "http://embed",
        "api_key": "sk-embed",
        "model": "text-embedding-v4",
        "dimensions": 1024,
    }


def test_memory_embedding_is_none_when_not_configured():
    """未配置 embedding 时 payload.memory.embedding 为 None（降级路径）。"""
    progress_calls = []
    _install_stubs(progress_calls)
    RECORDED_STREAM_PAYLOADS.clear()

    from libs.error_code.exception import CustomError

    async def resolve_llm(tenant_id, **kwargs):
        if kwargs.get("purpose") == "embedding":
            raise CustomError(4000, "未配置 embedding 模型")
        return SimpleNamespace(config_id=None, model_name="test-model")

    sys.modules["services.llm_config.resolver"].resolve_llm = resolve_llm
    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    task = FakeTask(
        id="task-no-embed", tenant_id="tenant-1", run_id=None, room_id=None,
        creator_id="c", source="manual", progress=0, status="pending",
        title="t", description="d", payload={}, result=None, saved_update_fields=[],
    )
    employee = SimpleNamespace(
        id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工"
    )

    asyncio.run(execute_coordinator.run(task, employee))

    assert len(RECORDED_STREAM_PAYLOADS) == 1
    memory = RECORDED_STREAM_PAYLOADS[0]["memory"]
    assert memory["embedding"] is None
