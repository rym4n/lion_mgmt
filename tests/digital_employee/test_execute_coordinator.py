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
    "services.digital_employee.cognee_credential_manager",
    "services.digital_employee.aioe_credential_manager",
    "services.digital_employee.prompt_builder",
    "services.digital_employee.lion_agent_adapter",
    "services.digital_employee.lion_agent_client",
    "services.digital_employee.execute_coordinator",
    "services.llm_config",
    "services.llm_config.resolver",
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
    settings_module.LION_MEMORY_ENDPOINT = "http://lion-memory"
    settings_module.WR_TOPIC = "wr-topic"
    settings_module.MEMORY_BACKEND = "reme"
    settings_module.MEMORY_BACKEND_COGNEE = "cognee"
    settings_module.MEMORY_BACKEND_REME = "reme"
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

    credential_manager = types.ModuleType("services.digital_employee.cognee_credential_manager")
    credential_manager.get_or_refresh_jwt = lambda tenant_id: _async_value(f"jwt-{tenant_id}")
    sys.modules["services.digital_employee.cognee_credential_manager"] = credential_manager

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
    resolver = types.ModuleType("services.llm_config.resolver")
    resolver.resolve_llm = lambda tenant_id, **kwargs: _async_value(
        SimpleNamespace(config_id=kwargs.get("config_id"), model_name="test-model")
    )
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
    assert payload["cognee"] is None
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


def _install_stubs_with_backend(progress_calls, backend: str):
    """按指定后端安装 stub。settings 是 execute_coordinator 的 from-import 来源，
    必须在 _load_module 之前把 MEMORY_BACKEND 设好。"""
    _install_stubs(progress_calls)
    sys.modules["settings"].MEMORY_BACKEND = backend


def _run_once(backend: str, *, continuation_workspace=None):
    progress_calls = []
    _install_stubs_with_backend(progress_calls, backend)
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


def test_cognee_backend_reproduces_pre_migration_payload_verbatim():
    """回滚路径：backend=cognee 时 cognee 段必须与迁移前逐字一致。"""
    payload = _run_once("cognee")

    assert payload["cognee"] == {
        "endpoint": "http://lion-memory",
        "jwt": "jwt-tenant-1",
        "tenant_id": "tenant-1",
        "dataset_name": "employee-1",
        "role_key": "ops",
        "task_id": "task-1",
        "source_kind": "manual",
    }


def test_cognee_backend_sends_no_memory_section():
    """回滚到旧 agent：旧 ExecuteRequest 的 extra='ignore' 会静默吞掉 memory，
    所以 cognee 后端下必须根本不发它，避免「以为发了、其实被忽略」。"""
    payload = _run_once("cognee")

    assert payload["memory"] is None


def test_reme_backend_sends_no_cognee_section():
    payload = _run_once("reme")

    assert payload["cognee"] is None
    assert payload["memory"]["employee_id"] == "employee-1"


def test_backends_are_mutually_exclusive_so_owner_conflict_is_impossible():
    """SPEC §5.7 第 3 条的 owner 冲突在 lion_agent 侧硬失败；
    lion_mgmt 侧的对应义务是：任一后端下都只发一个非 None 的段。"""
    for backend in ("cognee", "reme"):
        payload = _run_once(backend)
        sections = [payload["memory"], payload["cognee"]]
        assert sum(1 for section in sections if section is not None) == 1, backend


def test_cognee_backend_continuation_payload_unchanged():
    """SPEC §5.7 第 6 条第四项：continuation 场景的旧 payload 形状不变。"""
    payload = _run_once(
        "cognee",
        continuation_workspace={
            "workspace_id": "employee-1:approval-abc123def456:read-only",
            "files": {"system_prompt.md": "role"},
            "role_version": 3,
            "skill_versions": {},
        },
    )

    assert payload["workspace_id"] == "employee-1:approval-abc123def456:read-only"
    # 迁移前 dataset_name 就是 employee.id，与顶层 workspace_id 无关
    assert payload["cognee"]["dataset_name"] == "employee-1"
    assert payload["memory"] is None


def test_cognee_backend_does_not_resolve_embedding():
    """cognee 分支必须完全不碰 embedding 链路——它连 purpose='embedding' 都不该查，
    否则 P6 尚未落地时回滚路径会因未知 purpose 而多一次无谓查询。"""
    progress_calls = []
    _install_stubs_with_backend(progress_calls, "cognee")
    RECORDED_STREAM_PAYLOADS.clear()

    purposes = []
    original_resolve = sys.modules["services.llm_config.resolver"].resolve_llm

    def _spy(tenant_id, **kwargs):
        purposes.append(kwargs.get("purpose"))
        return original_resolve(tenant_id, **kwargs)

    sys.modules["services.llm_config.resolver"].resolve_llm = _spy

    execute_coordinator = _load_module(
        "services.digital_employee.execute_coordinator",
        "services/digital_employee/execute_coordinator.py",
    )
    execute_coordinator.caches = SimpleNamespace(get=lambda name: SimpleNamespace(client=FakeRedis()))

    task = FakeTask(
        id="task-1", tenant_id="tenant-1", run_id=None, room_id=None,
        creator_id="c", source="manual", progress=0, status="pending",
        title="t", description="d", payload={}, result=None, saved_update_fields=[],
    )
    employee = SimpleNamespace(id="employee-1", role_key="ops", agent_key="ops-1", name="运维")
    asyncio.run(execute_coordinator.run(task, employee))

    assert "embedding" not in purposes
