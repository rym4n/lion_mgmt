# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types

import orjson


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
    settings_module.LION_MEMORY_ENDPOINT = "http://lion-memory"
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

    credential_manager = types.ModuleType("services.digital_employee.cognee_credential_manager")
    credential_manager.get_or_refresh_jwt = lambda tenant_id: _async_value(f"jwt-{tenant_id}")
    sys.modules["services.digital_employee.cognee_credential_manager"] = credential_manager

    prompt_builder = types.ModuleType("services.digital_employee.prompt_builder")
    prompt_builder.build_runtime_prompt = lambda task, employee: f"prompt:{task.id}:{employee.id}"
    sys.modules["services.digital_employee.prompt_builder"] = prompt_builder

    adapter = types.ModuleType("services.digital_employee.lion_agent_adapter")
    adapter.stream_prompt_events = _stream_prompt_events
    sys.modules["services.digital_employee.lion_agent_adapter"] = adapter

    lion_agent_client = types.ModuleType("services.digital_employee.lion_agent_client")

    class LionAgentClient:
        async def sync_workspace(self, workspace_id, files, role_version=None, skill_versions=None):
            workspace_sync_calls.append(
                {
                    "workspace_id": workspace_id,
                    "files": files,
                    "role_version": role_version,
                    "skill_versions": skill_versions or {},
                }
            )
            return {"workspace_id": workspace_id, "etag": "readonly-etag", "file_count": len(files)}

    lion_agent_client.LionAgentClient = LionAgentClient
    lion_agent_client.workspace_sync_calls = workspace_sync_calls
    sys.modules["services.digital_employee.lion_agent_client"] = lion_agent_client


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
    employee = SimpleNamespace(id="employee-1", role_key="ops", agent_key="ops-1", name="运维员工")

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
    assert payload["cognee"]["endpoint"] == "http://lion-memory"
    assert payload["cognee"]["jwt"] == "jwt-tenant-1"
    assert payload["cognee"]["tenant_id"] == "tenant-1"
    assert payload["cognee"]["dataset_name"] == "employee-1"
    assert payload["cognee"]["role_key"] == "ops"

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
