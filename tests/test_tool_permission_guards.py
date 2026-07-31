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


@pytest.fixture(autouse=True)
def default_application_scope(monkeypatch):
    async def current_scope(_request):
        return False

    monkeypatch.setattr(tool_views, "_current_maintenance_mode", current_scope)


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

    async def ensure_resource(*_args, **_kwargs):
        return SimpleNamespace(id="resource-1")

    monkeypatch.setattr(tool_views, "ActionPermissions", FakeActionPermissions)
    monkeypatch.setattr(tool_views, "in_transaction", lambda: FakeTransaction())
    monkeypatch.setattr(tool_views, "_ensure_resource_in_scope", ensure_resource)
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


@pytest.mark.asyncio
async def test_select_permissions_for_new_resource_returns_scoped_empty_grants(
    monkeypatch,
):
    calls = []

    async def ensure_tool(tenant_id, maintenance_mode, **kwargs):
        calls.append(("tool", tenant_id, maintenance_mode, kwargs))

    async def permission_specs(tool_name, tenant_id, maintenance_mode):
        calls.append(("actions", tool_name, tenant_id, maintenance_mode))
        return [
            ("query", "查询"),
            ("all", "动作全部动作权限"),
        ]

    async def reject_resource_lookup(*_args, **_kwargs):
        raise AssertionError("new resources do not have an id to look up")

    monkeypatch.setattr(tool_views, "_ensure_tool_in_scope", ensure_tool)
    monkeypatch.setattr(
        tool_views,
        "_tool_action_permission_specs",
        permission_specs,
    )
    monkeypatch.setattr(
        tool_views,
        "_ensure_resource_in_scope",
        reject_resource_lookup,
    )
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    request = SimpleNamespace(
        json={"name": "sample_tool"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
    )
    result = await inspect.unwrap(tool_views.select_permission_all)(request)

    assert result == [
        {
            "name": "query",
            "description": "查询",
            "users_id": [],
            "roles_id": [],
        },
        {
            "name": "all",
            "description": "动作全部动作权限",
            "users_id": [],
            "roles_id": [],
        },
    ]
    assert calls == [
        ("tool", "tenant-1", False, {"name": "sample_tool"}),
        ("actions", "sample_tool", "tenant-1", False),
    ]


@pytest.mark.asyncio
async def test_select_permissions_for_existing_resource_keeps_grant_readback(
    monkeypatch,
):
    permission = SimpleNamespace(
        action_name="query",
        action_description="查询",
        users=[{"resource-1": ["user-1"]}],
        roles=[{"resource-1": ["role-1"]}],
    )

    class FakeQuery:
        async def all(self):
            return [permission]

    class FakeActionPermissions:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "tool_name": "sample_tool",
                "deleted": False,
                "tenant_id": "tenant-1",
            }
            return FakeQuery()

    async def ensure_resource(*args, **kwargs):
        assert args == ("tenant-1", False)
        assert kwargs == {
            "tool_name": "sample_tool",
            "resource_id": "resource-1",
        }

    async def ensure_permission(*_args, **_kwargs):
        return permission, False

    monkeypatch.setattr(tool_views, "ActionPermissions", FakeActionPermissions)
    monkeypatch.setattr(tool_views, "_ensure_resource_in_scope", ensure_resource)
    monkeypatch.setattr(
        tool_views,
        "_ensure_action_permission",
        ensure_permission,
    )
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    request = SimpleNamespace(
        json={"name": "sample_tool", "resource_id": "resource-1"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
    )
    result = await inspect.unwrap(tool_views.select_permission_all)(request)

    assert result == [
        {
            "name": "query",
            "description": "查询",
            "users_id": ["user-1"],
            "roles_id": ["role-1"],
        }
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


def test_add_action_permission_requires_resource_update_permission():
    source = inspect.getsource(tool_views.add_permission)

    assert "@requires_permission(TOOL_UPDATE_RESOURCE_PERMISSION)" in source


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

    await tool_views._apply_tool_group_internal_flags(
        tools,
        "tenant-1",
        False,
    )

    assert tools == [
        {"name": "builtin_tool", "internal": True},
        {"name": "custom_tool", "internal": False},
    ]
    assert filters == [
        {
            "name__in": ["builtin_tool", "custom_tool"],
            "internal": True,
            "maintain": False,
            "deleted": False,
            "tenant_id": "tenant-1",
        }
    ]


def test_tool_lists_are_scoped_by_server_maintenance_mode():
    all_tools_source = inspect.getsource(inspect.unwrap(tool_views.all_tools))
    all_versions_source = inspect.getsource(inspect.unwrap(tool_views.all_versions))

    assert "maintenance_mode = await _current_maintenance_mode(request)" in all_tools_source
    assert "maintain=maintenance_mode" in all_tools_source
    assert "maintenance_mode = await _current_maintenance_mode(request)" in all_versions_source
    assert "maintain=maintenance_mode" in all_versions_source


@pytest.mark.asyncio
async def test_tool_actions_returns_all_actions_when_legacy_filter_is_true(
    monkeypatch,
):
    class FakeTool:
        @staticmethod
        async def get_or_none(**kwargs):
            assert kwargs == {
                "name": "sample_tool",
                "version": "version-1",
                "deleted": False,
                "maintain": False,
                "tenant_id": "tenant-1",
            }
            return SimpleNamespace(id="tool-1")

    class FakeActionQuery:
        async def values(self, *fields):
            assert fields == ("action", "id", "playbook")
            return [
                {
                    "id": "action-1",
                    "action": {"func": "query", "name": "查询"},
                    "playbook": [],
                },
                {
                    "id": "action-2",
                    "action": {"func": "update", "name": "更新"},
                    "playbook": [],
                },
            ]

    class FakeToolAction:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "tool_name": "sample_tool",
                "version_number": "version-1",
                "tenant_id": "tenant-1",
            }
            return FakeActionQuery()

    monkeypatch.setattr(tool_views, "Tool", FakeTool)
    monkeypatch.setattr(tool_views, "ToolAction", FakeToolAction)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    request = SimpleNamespace(
        json={
            "name": "sample_tool",
            "version": "version-1",
            "filter_by_permission": True,
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
    )

    result = await inspect.unwrap(tool_views.tool_actions)(request)

    assert [action["func"] for action in result["actions"]] == [
        "query",
        "update",
    ]


@pytest.mark.asyncio
async def test_tool_resources_marks_permission_without_hiding_resources(
    monkeypatch,
):
    resources = [
        SimpleNamespace(
            id="resource-1",
            json={"id": "resource-1", "name": "已授权资源"},
        ),
        SimpleNamespace(
            id="resource-2",
            json={"id": "resource-2", "name": "未授权资源"},
        ),
    ]
    permission = SimpleNamespace(
        users=[{"resource-1": ["user-1"]}],
        roles=[],
    )

    class FakeResourceQuery:
        async def all(self):
            return resources

    class FakeToolResources:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "tool_name": "sample_tool",
                "deleted": False,
                "internal": False,
                "tenant_id": "tenant-1",
            }
            return FakeResourceQuery()

    class FakeToolActionQuery:
        async def values(self, *fields):
            assert fields == ("action", "tool_name", "version_number")
            return [
                {
                    "action": {"func": "query", "name": "查询"},
                    "tool_name": "sample_tool",
                    "version_number": "version-1",
                }
            ]

    class FakeToolAction:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "tool_name": "sample_tool",
                "deleted": False,
                "tenant_id": "tenant-1",
            }
            return FakeToolActionQuery()

    class FakePermissionQuery:
        async def all(self):
            return [permission]

    class FakeActionPermissions:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "tool_name": "sample_tool",
                "action_name__in": ["query", "all"],
                "deleted": False,
                "tenant_id": "tenant-1",
            }
            return FakePermissionQuery()

    async def cache_get(_key):
        return False

    async def scoped_tool_pairs(*_args, **_kwargs):
        return {("sample_tool", "version-1")}

    cache_client = SimpleNamespace(get=cache_get)
    account = SimpleNamespace(id="user-1", roles=[])
    request = SimpleNamespace(
        json={
            "name": "sample_tool",
            "action_name": "query",
            "filter_by_permission": True,
        },
        ctx=SimpleNamespace(
            session={
                "id": "session-1",
                "tenant_id": "tenant-1",
                "account_info": account,
            }
        ),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=cache_client)),
    )

    monkeypatch.setattr(tool_views, "ToolResources", FakeToolResources)
    monkeypatch.setattr(tool_views, "ToolAction", FakeToolAction)
    monkeypatch.setattr(tool_views, "ActionPermissions", FakeActionPermissions)
    monkeypatch.setattr(tool_views, "_scoped_tool_pairs", scoped_tool_pairs)
    monkeypatch.setattr(tool_views, "is_superadmin", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    result = await inspect.unwrap(tool_views.tool_resources_detailed)(request)

    assert result == [
        {
            "id": "resource-1",
            "name": "已授权资源",
            "has_permission": True,
        },
        {
            "id": "resource-2",
            "name": "未授权资源",
            "has_permission": False,
        },
    ]


@pytest.mark.asyncio
async def test_tool_resources_accepts_action_display_name_for_permission(
    monkeypatch,
):
    resources = [
        SimpleNamespace(id="resource-1", json={"id": "resource-1"}),
    ]
    permission = SimpleNamespace(
        users=[{"resource-1": ["user-1"]}],
        roles=[],
    )

    class FakeResourceQuery:
        async def all(self):
            return resources

    class FakeToolResources:
        @staticmethod
        def filter(**_kwargs):
            return FakeResourceQuery()

    class FakeToolActionQuery:
        async def values(self, *fields):
            assert fields == ("action", "tool_name", "version_number")
            return [
                {
                    "action": {"func": "query", "name": "查询"},
                    "tool_name": "sample_tool",
                    "version_number": "version-1",
                }
            ]

    class FakeToolAction:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "tool_name": "sample_tool",
                "deleted": False,
                "tenant_id": "tenant-1",
            }
            return FakeToolActionQuery()

    class FakePermissionQuery:
        async def all(self):
            return [permission]

    class FakeActionPermissions:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "tool_name": "sample_tool",
                "action_name__in": ["查询", "query", "all"],
                "deleted": False,
                "tenant_id": "tenant-1",
            }
            return FakePermissionQuery()

    async def cache_get(_key):
        return False

    async def scoped_tool_pairs(*_args, **_kwargs):
        return {("sample_tool", "version-1")}

    request = SimpleNamespace(
        json={
            "name": "sample_tool",
            "action_name": "查询",
            "filter_by_permission": True,
        },
        ctx=SimpleNamespace(
            session={
                "id": "session-1",
                "tenant_id": "tenant-1",
                "account_info": SimpleNamespace(id="user-1", roles=[]),
            }
        ),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=SimpleNamespace(get=cache_get))),
    )

    monkeypatch.setattr(tool_views, "ToolResources", FakeToolResources)
    monkeypatch.setattr(tool_views, "ToolAction", FakeToolAction)
    monkeypatch.setattr(tool_views, "ActionPermissions", FakeActionPermissions)
    monkeypatch.setattr(tool_views, "_scoped_tool_pairs", scoped_tool_pairs)
    monkeypatch.setattr(tool_views, "is_superadmin", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    result = await inspect.unwrap(tool_views.tool_resources_detailed)(request)

    assert result == [{"id": "resource-1", "has_permission": True}]


@pytest.mark.asyncio
async def test_add_tool_resource_grants_creator_all_action_permissions(
    monkeypatch,
):
    created_resources = []
    created_permissions = []
    bulk_updates = []
    cache_sets = []
    utime_updates = []

    class FakeTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeToolResources:
        @staticmethod
        async def get_or_none(**kwargs):
            assert kwargs == {
                "name": "资源A",
                "deleted": False,
                "tool_name": "sample_tool",
                "internal": False,
                "tenant_id": "tenant-1",
            }
            return None

        @staticmethod
        async def create(**kwargs):
            created_resources.append(kwargs)
            return SimpleNamespace(id="resource-1")

    class FakeToolActionQuery:
        async def values(self, *fields):
            assert fields == ("action", "tool_name", "version_number")
            return [
                {
                    "action": {"func": "query", "name": "查询"},
                    "tool_name": "sample_tool",
                    "version_number": "version-1",
                },
                {
                    "action": {"func": "update", "name": "更新"},
                    "tool_name": "sample_tool",
                    "version_number": "version-1",
                },
            ]

    class FakeToolAction:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "tool_name": "sample_tool",
                "deleted": False,
                "tenant_id": "tenant-1",
            }
            return FakeToolActionQuery()

    class FakeActionPermissions:
        @staticmethod
        async def get_or_create(**kwargs):
            permission = SimpleNamespace(
                action_name=kwargs["action_name"],
                users=[],
                roles=[],
            )
            created_permissions.append((kwargs, permission))
            return permission, False

        @staticmethod
        async def bulk_update(permissions, fields):
            bulk_updates.append((permissions, fields))

    class FakeRedis:
        async def set(self, key, value):
            cache_sets.append((key, value))

    async def cache_get(_key):
        return False

    async def update_utime(tool_name, tenant_id, maintenance_mode):
        utime_updates.append((tool_name, tenant_id, maintenance_mode))

    async def ensure_tool(*_args, **_kwargs):
        return SimpleNamespace(name="sample_tool")

    async def scoped_tool_pairs(*_args, **_kwargs):
        return {("sample_tool", "version-1")}

    request = SimpleNamespace(
        json={
            "tool_name": "sample_tool",
            "name": "资源A",
            "engine": "python",
            "resources": {"api_token": {"value": "secret"}},
        },
        ctx=SimpleNamespace(
            session={
                "id": "session-1",
                "tenant_id": "tenant-1",
                "account_info": SimpleNamespace(id="user-1", username="alice"),
            }
        ),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=SimpleNamespace(get=cache_get))),
    )

    monkeypatch.setattr(tool_views, "ToolResources", FakeToolResources)
    monkeypatch.setattr(tool_views, "ToolAction", FakeToolAction)
    monkeypatch.setattr(tool_views, "ActionPermissions", FakeActionPermissions)
    monkeypatch.setattr(tool_views, "_ensure_tool_in_scope", ensure_tool)
    monkeypatch.setattr(tool_views, "_scoped_tool_pairs", scoped_tool_pairs)
    monkeypatch.setattr(tool_views, "in_transaction", lambda: FakeTransaction())
    monkeypatch.setattr(tool_views.caches, "get", lambda _name: FakeRedis())
    monkeypatch.setattr(tool_views, "update_latest_tool_utime", update_utime)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    result = await inspect.unwrap(tool_views.add_tool_resource)(request)

    assert result == {"message": "工具资源新增成功", "id": "resource-1"}
    assert created_resources[0]["creator"] == "alice"
    assert created_resources[0]["tenant_id"] == "tenant-1"
    assert [item[0]["action_name"] for item in created_permissions] == [
        "query",
        "update",
        "all",
    ]
    assert bulk_updates[0][1] == ["users"]
    assert {
        permission.action_name: permission.users
        for permission in bulk_updates[0][0]
    } == {
        "query": [{"resource-1": ["user-1"]}],
        "update": [{"resource-1": ["user-1"]}],
        "all": [{"resource-1": ["user-1"]}],
    }
    assert cache_sets == [
        (
            "resource_resource-1",
            {"api_token": "secret", "resource-1_name": "资源A"},
        )
    ]
    assert utime_updates == [("sample_tool", "tenant-1", False)]


@pytest.mark.asyncio
async def test_tool_resources_marks_every_resource_allowed_for_superadmin(
    monkeypatch,
):
    resources = [
        SimpleNamespace(id="resource-1", json={"id": "resource-1"}),
        SimpleNamespace(id="resource-2", json={"id": "resource-2"}),
    ]

    class FakeResourceQuery:
        async def all(self):
            return resources

    class FakeToolResources:
        @staticmethod
        def filter(**_kwargs):
            return FakeResourceQuery()

    class RejectPermissionQuery:
        @staticmethod
        def filter(**_kwargs):
            raise AssertionError("superadmin must not query action permissions")

    async def cache_get(_key):
        return False

    request = SimpleNamespace(
        json={
            "name": "sample_tool",
            "action_name": "query",
            "filter_by_permission": True,
        },
        ctx=SimpleNamespace(
            session={
                "id": "session-1",
                "tenant_id": "tenant-1",
                "account_info": SimpleNamespace(id="admin-1", roles=[]),
            }
        ),
        app=SimpleNamespace(
            ctx=SimpleNamespace(
                cache_client=SimpleNamespace(get=cache_get),
            )
        ),
    )

    monkeypatch.setattr(tool_views, "ToolResources", FakeToolResources)
    monkeypatch.setattr(tool_views, "ActionPermissions", RejectPermissionQuery)
    monkeypatch.setattr(tool_views, "is_superadmin", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    result = await inspect.unwrap(tool_views.tool_resources_detailed)(request)

    assert result == [
        {"id": "resource-1", "has_permission": True},
        {"id": "resource-2", "has_permission": True},
    ]


@pytest.mark.asyncio
async def test_tool_resources_marks_every_resource_allowed_for_uncontrolled_role(
    monkeypatch,
):
    resources = [
        SimpleNamespace(id="resource-1", json={"id": "resource-1"}),
        SimpleNamespace(id="resource-2", json={"id": "resource-2"}),
    ]

    class FakeResourceQuery:
        async def all(self):
            return resources

    class FakeToolResources:
        @staticmethod
        def filter(**_kwargs):
            return FakeResourceQuery()

    class RejectActionPermissions:
        @staticmethod
        def filter(**_kwargs):
            raise AssertionError("uncontrolled role must not query action grants")

    async def cache_get(_key):
        return False

    request = SimpleNamespace(
        json={
            "name": "sample_tool",
            "action_name": "query",
            "filter_by_permission": True,
        },
        ctx=SimpleNamespace(
            session={
                "id": "session-1",
                "tenant_id": "tenant-1",
                "account_info": SimpleNamespace(
                    id="user-1",
                    roles=[
                        SimpleNamespace(
                            id="role-1",
                            permissions=["tool:r", "tool:resource:uncontrolled"],
                            tenant_id="tenant-1",
                        )
                    ],
                ),
            }
        ),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=SimpleNamespace(get=cache_get))),
    )

    monkeypatch.setattr(tool_views, "ToolResources", FakeToolResources)
    monkeypatch.setattr(tool_views, "ActionPermissions", RejectActionPermissions)
    monkeypatch.setattr(tool_views, "is_superadmin", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    result = await inspect.unwrap(tool_views.tool_resources_detailed)(request)

    assert result == [
        {"id": "resource-1", "has_permission": True},
        {"id": "resource-2", "has_permission": True},
    ]


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

    versions = await tool_views._lock_tool_versions(
        "sample_tool",
        "tenant-1",
        False,
    )

    assert versions == ["version-1", "version-2"]
    assert operations == [
        (
            "filter",
            {
                "name": "sample_tool",
                "tenant_id": "tenant-1",
                "maintain": False,
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

    async def cache_get(_key):
        return False

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
                "id": "session-1",
                "account_info": user,
                "tenant_id": "tenant-1",
            }
        ),
        app=SimpleNamespace(
            ctx=SimpleNamespace(cache_client=SimpleNamespace(get=cache_get))
        ),
    )

    with pytest.raises(CustomError) as exc_info:
        await inspect.unwrap(warroom_views.run)(request)

    assert exc_info.value.enum is ErrorCode.UNAUTH
    assert permission_args == [
        (
            user,
            "sample_tool",
            "query",
            "resource-1",
            "tenant-1",
            False,
            None,
        )
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

    async def cache_get(_key):
        return False

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
                "id": "session-1",
                "account_info": SimpleNamespace(id="user-1"),
                "tenant_id": "tenant-1",
            }
        ),
        app=SimpleNamespace(
            ctx=SimpleNamespace(cache_client=SimpleNamespace(get=cache_get))
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

    async def cache_get(_key):
        return False

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
                "id": "session-1",
                "account_info": SimpleNamespace(id="user-1"),
                "tenant_id": "tenant-1",
            }
        ),
        app=SimpleNamespace(
            ctx=SimpleNamespace(cache_client=SimpleNamespace(get=cache_get))
        ),
    )

    with pytest.raises(CustomError) as exc_info:
        await inspect.unwrap(warroom_views.execution)(request)

    assert exc_info.value.enum is ErrorCode.UNAUTH
    assert produced == []
