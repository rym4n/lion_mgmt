# coding=utf-8
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.tools import resource_health_check as health
from services.tools import views
from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
import inspect


@pytest.mark.asyncio
async def test_capability_reads_latest_scoped_version_and_actual_action(monkeypatch):
    seen = {}

    class Query:
        def order_by(self, value):
            seen["order"] = value
            return self

        async def first(self):
            return SimpleNamespace(name="demo", version="v2")

    class Tools:
        @staticmethod
        def filter(**kwargs):
            seen["tools"] = kwargs
            return Query()

    class Actions:
        @staticmethod
        def filter(**kwargs):
            seen["actions"] = kwargs
            return SimpleNamespace(all=lambda: _actions())

    async def _actions():
        return [SimpleNamespace(action={"func": "health_check"})]

    monkeypatch.setattr(health, "Tool", Tools)
    monkeypatch.setattr(health, "ToolAction", Actions)
    assert await health.health_check_capability("demo", "tenant-a", False) == {
        "supported": True,
        "version": "v2",
    }
    assert seen["order"] == "-ctime"
    assert seen["tools"]["maintain"] is False
    assert seen["actions"]["version_number"] == "v2"


def test_resource_values_preserve_false_zero_and_defaults():
    schema = {
        "enabled": {"data_type": "boolean", "default_value": True},
        "count": {"data_type": "integer", "default_value": 5},
        "host": {"data_type": "string", "required": False, "default_value": "fallback"},
    }
    assert health.resource_values(schema, {
        "enabled": {"value": False},
        "count": {"value": 0},
        "host": {"value": ""},
    })["enabled"] is False
    assert health.resource_values(schema, {
        "enabled": {"value": False},
        "count": {"value": 0},
        "host": {"value": "localhost"},
    })["count"] == 0
    assert health.resource_values(schema, {"host": {"default_value": "client-forged"}})["host"] == "fallback"


@pytest.mark.parametrize("field,value", [
    ({"data_type": "string", "required": True}, ""),
    ({"data_type": "integer"}, 1.5),
    ({"data_type": "integer"}, "invalid"),
    ({"data_type": "number"}, float("inf")),
    ({"data_type": "number", "constraints": {"minimum": 2}}, 1),
    ({"data_type": "integer", "constraints": {"maximum": 10}}, 11),
    ({"data_type": "string", "constraints": {"maxLength": 2}}, "abc"),
    ({"data_type": "password"}, "******"),
])
def test_invalid_current_resource_values_are_rejected_by_server_schema(field, value):
    with pytest.raises(CustomError) as exc_info:
        health.resource_values({"value": field}, {"value": {"value": value}})
    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


@pytest.mark.parametrize("result,expected", [
    ('{"status":0}', True),
    ('{"status":false}', False),
    ('{"code":false}', False),
    ('{"code":0,"data":{"status":"failed"}}', False),
    ('false', False), ('null', False), ('{}', False), ('', False), ('执行中', False),
])
def test_result_status_and_false_empty_values_are_not_misclassified(result, expected):
    assert health.health_check_succeeded(0, result) is expected


@pytest.mark.parametrize("result,expected", [
    ("False", False), ("None", False), ("True", True),
    ('{"code":null}', False), ('{"status":""}', False),
    ('{"success":null}', False), ('{"success":"false"}', False),
    ('{"code":0,"data":false}', False),
])
def test_connection_result_handles_actual_runner_scalar_serialization(result, expected):
    # Flow.send_run_log uses str() for scalar output, JSON for dictionaries.
    assert health.health_check_succeeded(0, result) is expected


@pytest.fixture
def execution_environment(monkeypatch):
    state = {"cached": {}, "deleted": [], "messages": [], "filters": [], "logs": []}

    class Cache:
        async def set(self, key, value, ttl):
            state["cached"][key] = (value.copy(), ttl)

        async def delete(self, key):
            state["deleted"].append(key)

    class LogQuery:
        def order_by(self, *_fields):
            return self

        async def first(self):
            return state["logs"].pop(0) if state["logs"] else None

    class Logs:
        @staticmethod
        def filter(**kwargs):
            state["filters"].append(kwargs)
            return LogQuery()

    async def publish(topic, message, **kwargs):
        assert kwargs == {"raise_on_error": True}
        state["messages"].append((topic, message))
        if state.get("publish_error"):
            raise RuntimeError("sensitive details")

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(health, "caches", SimpleNamespace(get=lambda _name: Cache()))
    monkeypatch.setattr(health, "Runlog", Logs)
    monkeypatch.setattr(health, "aproduce", publish)
    monkeypatch.setattr(health.asyncio, "sleep", no_sleep)
    return state


async def _execute():
    return await health.execute_health_check(
        tool=SimpleNamespace(name="demo", version="v2", main="server.py", description="Demo"),
        action={"func": "health_check", "name": "Health", "input": []},
        values={"password": "unsaved-secret", "enabled": False},
        name="Draft resource", engine="engine-x", tenant_id="tenant-a", executor="user-a",
    )


@pytest.mark.asyncio
async def test_execution_uses_temporary_resource_selected_engine_and_scoped_log(execution_environment):
    state = execution_environment
    state["logs"] = [SimpleNamespace(status=1, result="执行中"), SimpleNamespace(status=0, result='{"code":0}')]
    result = await _execute()
    assert result["success"] is True
    assert set(result) == {"success", "run_id"}
    _, message = state["messages"][0]
    args = message["run_args"]
    assert message["run_type"] == "action"
    assert message["tenant_id"] == "tenant-a"
    assert args["action"] == "health_check"
    assert args["main"] == "server.py"
    assert args["app_ver"] == "v2"
    assert args["action_engine"] == "engine-x"
    assert args["timeout"] == 60
    assert args["retry_times"] == 0
    assert args["action_input"] == {}
    assert "unsaved-secret" not in repr(message)
    key = "resource_" + args["resource"]
    values, ttl = state["cached"][key]
    assert values == {"password": "unsaved-secret", "enabled": False, args["resource"] + "_name": "Draft resource"}
    assert ttl >= 120
    assert state["deleted"] == [key]
    assert all(filters == {
        "run_id": result["run_id"], "tenant_id": "tenant-a", "deleted": False,
        "node_type": "action-node", "tool_name": "demo", "tool_version": "v2",
        "action_name": "health_check",
    } for filters in state["filters"])


@pytest.mark.asyncio
async def test_execution_timeout_never_reports_success_and_cleans_cache(execution_environment):
    state = execution_environment
    with pytest.raises(CustomError) as exc_info:
        await _execute()
    assert exc_info.value.enum is ErrorCode.ACTION_TIMEOUT
    assert len(state["deleted"]) == 1
    assert len(state["filters"]) == 60


@pytest.mark.asyncio
async def test_execution_dispatch_failure_is_generic_and_cleans_cache(execution_environment):
    state = execution_environment
    state["publish_error"] = True
    result = await _execute()
    assert result["success"] is False
    assert result["run_id"]
    assert "sensitive" not in repr(result)
    assert len(state["deleted"]) == 1


@pytest.mark.asyncio
async def test_dispatch_wait_is_bounded_and_temporary_cache_is_cleaned(execution_environment, monkeypatch):
    async def stalled_publish(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(health, "aproduce", stalled_publish)
    monkeypatch.setattr(health, "_POLL_SECONDS", 0.01)
    with pytest.raises(CustomError) as exc_info:
        await asyncio.wait_for(_execute(), timeout=0.2)
    assert exc_info.value.enum is ErrorCode.ACTION_TIMEOUT
    assert len(execution_environment["deleted"]) == 1


@pytest.fixture
def route_environment(monkeypatch):
    state = {"permissions": [], "dispatched": [], "existing": None, "allowed": True}

    async def mode(_request):
        return False

    async def permission(_request, *permissions, **_kwargs):
        state["permissions"].extend(permissions)
        if state.get("deny_permission") in permissions:
            raise CustomError(ErrorCode.PARAM_ERROR)

    async def resolve(tool_name, version, tenant_id, maintenance_mode):
        assert (tool_name, version, tenant_id, maintenance_mode) == ("demo", "v2", "tenant-a", False)
        return SimpleNamespace(name="demo", version="v2", main="main.py"), {"func": "health_check", "input": []}

    async def existing(tenant_id, maintenance_mode, **kwargs):
        assert (tenant_id, maintenance_mode) == ("tenant-a", False)
        assert kwargs == {"tool_name": "demo", "resource_id": "existing-id"}
        if state["existing"] is None:
            raise CustomError(ErrorCode.RESOURCES_NOT_FOUND_ERROR)
        return state["existing"]

    async def allowed(*args):
        state["action_permission"] = args
        return state["allowed"]

    async def schema(*args):
        assert args == ("demo", "tenant-a", False)
        return SimpleNamespace(resources={"token": {"data_type": "password", "required": True}})

    async def execute(**kwargs):
        state["dispatched"].append(kwargs)
        return {"success": True, "run_id": "run-a"}

    monkeypatch.setattr(views, "_current_maintenance_mode", mode)
    monkeypatch.setattr(views, "check_permissions", permission)
    monkeypatch.setattr(views, "_ensure_resource_in_scope", existing)
    monkeypatch.setattr(views, "check_action_execute_permission", allowed)
    monkeypatch.setattr(views, "response_ok", lambda data: data)
    monkeypatch.setattr(health, "_resource_schema", schema)
    monkeypatch.setattr(health, "resolve_health_check", resolve)
    monkeypatch.setattr(health, "execute_health_check", execute)
    return state


def _request(**kwargs):
    return SimpleNamespace(
        json={"tool_name": "demo", "name": "Draft", "resources": {"token": {"value": "current-secret"}}, "version": "v2", **kwargs},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a", "account_info": SimpleNamespace(id="user-a")}),
    )


@pytest.mark.asyncio
async def test_new_resource_test_needs_create_permission_and_uses_current_values(route_environment):
    state = route_environment
    result = await inspect.unwrap(views.test_tool_resource_connection)(_request(action="destructive", main="untrusted.py"))
    assert result == {"success": True, "run_id": "run-a"}
    assert state["permissions"] == ["tool:resource:c"]
    assert state["dispatched"][0]["values"] == {"token": "current-secret"}
    assert state["dispatched"][0]["action"]["func"] == "health_check"


@pytest.mark.asyncio
async def test_edit_requires_update_and_action_permission_before_secret_restore(route_environment):
    state = route_environment
    state["existing"] = SimpleNamespace(engine="engine-old", resources_json={"token": {"value": "stored-secret"}})
    state["allowed"] = False
    with pytest.raises(CustomError):
        await inspect.unwrap(views.test_tool_resource_connection)(_request(resource_id="existing-id", resources={"token": {"value": "******"}}))
    assert state["permissions"] == ["tool:resource:u"]
    assert state["dispatched"] == []
    assert state["action_permission"][1:] == ("demo", "health_check", "existing-id", "tenant-a", False, "v2")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine,expected_permissions", [("engine-old", ["tool:resource:u"]), ("engine-new", ["tool:resource:u", "tool:engine:u"])])
async def test_edit_restores_scoped_secret_and_only_checks_changed_engine(route_environment, engine, expected_permissions):
    state = route_environment
    state["existing"] = SimpleNamespace(engine="engine-old", resources_json={"token": {"value": "stored-secret"}})
    result = await inspect.unwrap(views.test_tool_resource_connection)(_request(resource_id="existing-id", engine=engine, resources={"token": {"value": "******"}}))
    assert result["success"] is True
    assert state["permissions"] == expected_permissions
    assert state["dispatched"][0]["values"] == {"token": "stored-secret"}
    assert state["dispatched"][0]["engine"] == engine


@pytest.mark.asyncio
async def test_create_permission_cannot_test_existing_credentials(route_environment):
    state = route_environment
    state["deny_permission"] = "tool:resource:u"
    with pytest.raises(CustomError):
        await inspect.unwrap(views.test_tool_resource_connection)(_request(resource_id="existing-id"))
    assert state["dispatched"] == []


@pytest.mark.asyncio
async def test_cross_scope_resource_is_rejected_before_dispatch(route_environment):
    state = route_environment
    with pytest.raises(CustomError) as exc_info:
        await inspect.unwrap(views.test_tool_resource_connection)(_request(resource_id="existing-id", resources={"token": {"value": "******"}}))
    assert exc_info.value.enum is ErrorCode.RESOURCES_NOT_FOUND_ERROR
    assert state["dispatched"] == []


def test_masked_password_restores_only_existing_password():
    schema = {"token": {"data_type": "password"}}
    existing = {"token": {"data_type": "password", "value": "secret"}}
    submitted = {"token": {"data_type": "password", "value": "******"}}
    assert health.resource_values(schema, submitted, existing) == {"token": "secret"}
    assert submitted["token"]["value"] == "******"


def test_health_result_rejects_nested_failure_and_engine_failure():
    assert health.health_check_succeeded(0, '{"code":0,"data":{"code":401}}') is False
    assert health.health_check_succeeded(-1, '{"code":0}') is False
    assert health.health_check_succeeded(0, '{"code":0}') is True
