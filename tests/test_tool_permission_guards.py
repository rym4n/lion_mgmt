# coding=utf-8
import asyncio
import importlib
import inspect
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.tools import views as tool_views
from services.warroom import views as warroom_views
from services.workflow import views as workflow_views


permission_migration = importlib.import_module(
    "migrations.models.71_20260713113000_dedupe_action_permissions"
)


@pytest.mark.asyncio
async def test_ensure_action_permission_is_idempotent(monkeypatch):
    calls = []

    class FakeActionPermissions:
        @staticmethod
        async def get_or_create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(**kwargs), True

    monkeypatch.setattr(tool_views, "ActionPermissions", FakeActionPermissions)

    await tool_views._ensure_action_permission(
        "sample_tool",
        "all",
        "动作全部动作权限",
        "tenant-1",
    )

    assert calls == [
        {
            "tool_name": "sample_tool",
            "action_name": "all",
            "deleted": False,
            "tenant_id": "tenant-1",
            "defaults": {
                "action_description": "动作全部动作权限",
                "roles": [],
                "users": [],
            },
        }
    ]


@pytest.mark.asyncio
async def test_add_permission_locks_row_before_json_update(monkeypatch):
    operations = []
    permission = SimpleNamespace(
        users=[{"existing": ["user-0"]}],
        roles=[{"existing": ["role-0"]}],
    )

    class FakeTransaction:
        async def __aenter__(self):
            operations.append("transaction_enter")

        async def __aexit__(self, *_args):
            operations.append("transaction_exit")

    class FakeQuery:
        def select_for_update(self):
            operations.append("select_for_update")
            return self

        def order_by(self, field):
            operations.append(("order_by", field))
            return self

        async def all(self):
            operations.append("all")
            return [permission]

    class FakeActionPermissions:
        @staticmethod
        async def get_or_create(**_kwargs):
            operations.append("get_or_create")
            return permission, False

        @staticmethod
        def filter(**_kwargs):
            operations.append("filter")
            return FakeQuery()

        @staticmethod
        async def bulk_update(items, fields):
            operations.append(("bulk_update", items, fields))

    monkeypatch.setattr(tool_views, "ActionPermissions", FakeActionPermissions)
    monkeypatch.setattr(tool_views, "in_transaction", lambda: FakeTransaction())
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    request = SimpleNamespace(
        json={
            "name": "sample_tool",
            "resource_id": "resource-1",
            "actions": ["all"],
            "users_id": ["user-1"],
            "roles_id": ["role-1"],
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
    )

    result = await inspect.unwrap(tool_views.add_permission)(request)

    assert result == {"message": "工具动作权限新增成功"}
    assert operations[:6] == [
        "transaction_enter",
        "get_or_create",
        "filter",
        "select_for_update",
        ("order_by", "id"),
        "all",
    ]
    assert operations[-1] == "transaction_exit"
    assert permission.users == [
        {"existing": ["user-0"], "resource-1": ["user-1"]}
    ]
    assert permission.roles == [
        {"existing": ["role-0"], "resource-1": ["role-1"]}
    ]


def test_action_permission_migration_merges_grants_before_soft_delete():
    sql = asyncio.run(permission_migration.upgrade(None))

    assert 'LOCK TABLE "action_permissions" IN SHARE ROW EXCLUSIVE MODE' in sql
    assert 'jsonb_array_elements(permission."users")' in sql
    assert 'jsonb_array_elements(permission."roles")' in sql
    assert "LEFT JOIN LATERAL jsonb_array_elements_text(resource.value)" in sql
    assert "FILTER (WHERE granted_id IS NOT NULL)" in sql
    assert "jsonb_object_agg(resource_id, granted_ids ORDER BY resource_id)" in sql
    assert 'SET\n            "deleted" = True' in sql
    assert 'CREATE UNIQUE INDEX IF NOT EXISTS "uid_action_permissions_active_scope"' in sql
    assert "NULLS NOT DISTINCT" in sql
    assert 'WHERE "deleted" = False' in sql


@pytest.mark.parametrize(("maintain", "internal", "expected"), [(False, True, False), (False, False, False), (True, True, True)])
def test_tool_delete_only_protects_maintenance_applications(maintain, internal, expected):
    tool = SimpleNamespace(maintain=maintain, internal=internal)

    assert tool_views._should_reject_tool_version_delete(tool) is expected


def test_delete_all_only_protects_maintenance_applications():
    source = inspect.getsource(inspect.unwrap(tool_views.delete_tool_all))

    assert "if any(tool.maintain for tool in active_tools):" in source
    assert "tool_group_internal" not in source


@pytest.mark.asyncio
async def test_tool_list_exposes_historical_group_internal_identity(monkeypatch):
    filters = []
    tools = [
        {"name": "builtin_tool", "internal": False},
        {"name": "custom_tool", "internal": False},
    ]

    class FakeQuery:
        async def values_list(self, field, flat):
            assert (field, flat) == ("name", True)
            return ["builtin_tool"]

    class FakeTool:
        @staticmethod
        def filter(**kwargs):
            filters.append(kwargs)
            return FakeQuery()

    monkeypatch.setattr(tool_views, "Tool", FakeTool)

    await tool_views._apply_tool_group_internal_flags(tools, "tenant-1")

    assert tools == [
        {"name": "builtin_tool", "internal": True},
        {"name": "custom_tool", "internal": False},
    ]
    assert filters == [
        {
            "name__in": ["builtin_tool", "custom_tool"],
            "internal": True,
            "tenant_id": "tenant-1",
        }
    ]


def test_tool_lists_do_not_hide_versions_by_maintenance_flag():
    all_tools_source = inspect.getsource(inspect.unwrap(tool_views.all_tools))
    all_versions_source = inspect.getsource(inspect.unwrap(tool_views.all_versions))

    assert "maintenance_mode" not in all_tools_source
    assert "maintain=" not in all_tools_source
    assert "maintenance_mode" not in all_versions_source
    assert "maintain=" not in all_versions_source


def test_tool_action_filter_preserves_actions_without_resource_requirement():
    source = inspect.getsource(inspect.unwrap(tool_views.tool_actions))

    assert "await tool_requires_resource(name, tenant_id)" in source


@pytest.mark.asyncio
async def test_all_tool_versions_are_locked_in_stable_order(monkeypatch):
    operations = []

    class FakeQuery:
        def select_for_update(self):
            operations.append("select_for_update")
            return self

        def order_by(self, field):
            operations.append(("order_by", field))
            return self

        async def all(self):
            operations.append("all")
            return ["version-1", "version-2"]

    class FakeTool:
        @staticmethod
        def filter(**kwargs):
            operations.append(("filter", kwargs))
            return FakeQuery()

    monkeypatch.setattr(tool_views, "Tool", FakeTool)

    versions = await tool_views._lock_tool_versions("sample_tool", "tenant-1")

    assert versions == ["version-1", "version-2"]
    assert operations == [
        (
            "filter",
            {
                "name": "sample_tool",
                "tenant_id": "tenant-1",
            },
        ),
        "select_for_update",
        ("order_by", "id"),
        "all",
    ]


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_warroom_action_run_rejects_ungranted_resource_before_dispatch(monkeypatch):
    permission_args = []
    produced = []

    async def deny(*args):
        permission_args.append(args)
        return False

    async def produce(*args):
        produced.append(args)

    monkeypatch.setattr(warroom_views, "check_action_execute_permission", deny)
    monkeypatch.setattr(warroom_views, "aproduce", produce)

    user = SimpleNamespace(id="user-1")
    request = SimpleNamespace(
        json={
            "id": "room-1",
            "type": "action",
            "action_input": {
                "app_name": "sample_tool",
                "action": "query",
                "resource": "resource-1",
            },
        },
        ctx=SimpleNamespace(
            session={
                "account_info": user,
                "tenant_id": "tenant-1",
            }
        ),
    )

    with pytest.raises(CustomError) as exc_info:
        await inspect.unwrap(warroom_views.run)(request)

    assert exc_info.value.enum is ErrorCode.UNAUTH
    assert permission_args == [
        (user, "sample_tool", "query", "resource-1", "tenant-1")
    ]
    assert produced == []


@pytest.mark.asyncio
async def test_workflow_action_debug_rejects_ungranted_resource_before_dispatch(monkeypatch):
    produced = []

    class FakeResponse:
        def __init__(self):
            self.messages = []
            self.eof_called = False

        async def send(self, message):
            self.messages.append(message)

        async def eof(self):
            self.eof_called = True

    response = FakeResponse()

    async def respond(**_kwargs):
        return response

    async def deny(*_args):
        return False

    async def produce(*args):
        produced.append(args)

    monkeypatch.setattr(workflow_views, "check_action_execute_permission", deny)
    monkeypatch.setattr(workflow_views, "aproduce", produce)

    request = SimpleNamespace(
        respond=respond,
        json={
            "type": "action",
            "app_name": "sample_tool",
            "action": "query",
            "resource": "resource-1",
        },
        ctx=SimpleNamespace(
            session={
                "account_info": SimpleNamespace(id="user-1"),
                "tenant_id": "tenant-1",
            }
        ),
    )

    result = await inspect.unwrap(workflow_views.execute_action)(request)

    assert result is response
    assert response.messages == ["data: 缺少动作资源执行权限，执行失败\n\n"]
    assert response.eof_called is True
    assert produced == []


@pytest.mark.asyncio
async def test_tool_health_check_rejects_ungranted_resource_before_dispatch(monkeypatch):
    produced = []

    class FakeResponse:
        def __init__(self):
            self.messages = []
            self.eof_called = False

        async def send(self, message):
            self.messages.append(message)

        async def eof(self):
            self.eof_called = True

    response = FakeResponse()

    async def respond(**_kwargs):
        return response

    async def deny(*_args):
        return False

    async def produce(*args):
        produced.append(args)

    monkeypatch.setattr(tool_views, "check_action_execute_permission", deny)
    monkeypatch.setattr(tool_views, "aproduce", produce)

    request = SimpleNamespace(
        respond=respond,
        json={
            "action_input": {
                "type": "action",
                "app_name": "sample_tool",
                "action": "health_check",
                "resource": "resource-1",
            }
        },
        ctx=SimpleNamespace(
            session={
                "account_info": SimpleNamespace(id="user-1"),
                "tenant_id": "tenant-1",
            }
        ),
    )

    result = await inspect.unwrap(tool_views.health_check)(request)

    assert result is response
    assert response.messages == ["data: 缺少动作资源执行权限，执行失败\n\n"]
    assert response.eof_called is True
    assert produced == []


@pytest.mark.asyncio
async def test_legacy_warroom_execution_checks_action_permission_before_room_creation(
    monkeypatch,
):
    produced = []

    class FakeEvents:
        @staticmethod
        async def get_or_none(**_kwargs):
            return SimpleNamespace(room=None, event_name="event")

    class FakeToolAction:
        @staticmethod
        async def get_or_none(**_kwargs):
            return SimpleNamespace(
                tool_name="sample_tool",
                version_number="1",
                action={"func": "query", "name": "查询"},
            )

    class FakeTool:
        @staticmethod
        async def get_or_none(**_kwargs):
            return SimpleNamespace(
                name="sample_tool",
                description="示例应用",
                version="1",
                main="main.py",
            )

    class FakeRoom:
        @staticmethod
        async def create(**_kwargs):
            raise AssertionError("permission denial must happen before room creation")

    async def deny(*_args):
        return False

    async def produce(*args):
        produced.append(args)

    monkeypatch.setattr(warroom_views, "Events", FakeEvents)
    monkeypatch.setattr(warroom_views, "ToolAction", FakeToolAction)
    monkeypatch.setattr(warroom_views, "Tool", FakeTool)
    monkeypatch.setattr(warroom_views, "Room", FakeRoom)
    monkeypatch.setattr(warroom_views, "check_action_execute_permission", deny)
    monkeypatch.setattr(warroom_views, "aproduce", produce)

    request = SimpleNamespace(
        json={
            "eventId": "event-1",
            "executorInstanceType": "ACTION",
            "executorInstanceId": "action-1",
            "executorActionAssetIds": "resource-1",
            "params": [],
        },
        ctx=SimpleNamespace(
            session={
                "account_info": SimpleNamespace(id="user-1"),
                "tenant_id": "tenant-1",
            }
        ),
    )

    with pytest.raises(CustomError) as exc_info:
        await inspect.unwrap(warroom_views.execution)(request)

    assert exc_info.value.enum is ErrorCode.UNAUTH
    assert produced == []
