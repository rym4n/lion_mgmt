# coding=utf-8
import inspect
from types import SimpleNamespace

import pytest

from services.user import views as user_views
from services.workflow import views as workflow_views


def test_playbook_permission_update_requires_fresh_vertical_permission():
    source = inspect.getsource(workflow_views.add_permission)

    assert '@requires_permission("workflow:u", refresh=True)' in source


@pytest.mark.asyncio
async def test_role_list_only_marks_fixed_superadmin_role(monkeypatch):
    roles = [
        SimpleNamespace(
            id="space-admin",
            name="空间管理员",
            permissions=["workflow:r", "workflow:u"],
        ),
        SimpleNamespace(id="tenant-admin", name="租户管理员", permissions=["*:*"]),
        SimpleNamespace(id="111111", name="系统管理员", permissions=["*:*"]),
    ]

    class FakeRoleQuery:
        async def all(self):
            return roles

    class FakeRole:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {"deleted": False, "tenant_id": "tenant-1"}
            return FakeRoleQuery()

    monkeypatch.setattr(user_views, "Role", FakeRole)
    monkeypatch.setattr(user_views, "response_ok", lambda data: data)

    request = SimpleNamespace(ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}))

    result = await user_views.init_role_list(request)

    assert result["roles"] == [
        {"id": "space-admin", "name": "空间管理员", "is_superadmin": False},
        {"id": "tenant-admin", "name": "租户管理员", "is_superadmin": False},
        {"id": "111111", "name": "系统管理员", "is_superadmin": True},
    ]


@pytest.mark.asyncio
async def test_account_list_only_marks_users_with_fixed_superadmin_role(monkeypatch):
    class FakeUser(SimpleNamespace):
        async def fetch_related(self, *fields):
            assert fields == ("roles",)

    users = [
        FakeUser(
            id="any-super-user",
            username="admin",
            display_name="管理员",
            deleted=False,
            type="user",
            roles=[SimpleNamespace(id="111111", permissions=["*:*"], tenant_id="default")],
        ),
        FakeUser(
            id="tenant-admin-user",
            username="tenant_admin",
            display_name="租户管理员",
            deleted=False,
            type="user",
            roles=[SimpleNamespace(id="tenant-admin", permissions=["*:*"], tenant_id="tenant-1")],
        ),
        FakeUser(
            id="normal-user",
            username="operator",
            display_name="操作员",
            deleted=False,
            type="user",
            roles=[SimpleNamespace(permissions=["workflow:r"], tenant_id="tenant-1")],
        ),
    ]

    class FakeTenantQuery:
        def prefetch_related(self, *fields):
            assert fields == ("member",)
            return self

        def __await__(self):
            async def value():
                return SimpleNamespace(member=users)

            return value().__await__()

    class FakeTenant:
        @staticmethod
        def get_or_none(**kwargs):
            assert kwargs == {"deleted": False, "id": "tenant-1"}
            return FakeTenantQuery()

    monkeypatch.setattr(user_views, "Tenant", FakeTenant)
    monkeypatch.setattr(user_views, "response_ok", lambda data: data)

    request = SimpleNamespace(ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}))

    result = await user_views.init_account_list(request)

    assert result["users"] == [
        {
            "id": "any-super-user",
            "username": "admin",
            "display_name": "管理员",
            "is_superadmin": True,
        },
        {
            "id": "tenant-admin-user",
            "username": "tenant_admin",
            "display_name": "租户管理员",
            "is_superadmin": False,
        },
        {
            "id": "normal-user",
            "username": "operator",
            "display_name": "操作员",
            "is_superadmin": False,
        },
    ]


@pytest.mark.asyncio
async def test_playbook_permission_update_skips_superadmin_users_and_roles(
    monkeypatch,
):
    updates = []
    cache_sets = []

    class FakePlaybook:
        internal = False

        @staticmethod
        async def get_or_none(**kwargs):
            assert kwargs == {"id": "playbook-1", "tenant_id": "tenant-1"}
            return FakePlaybook()

    class FakePlaybookPermissions:
        @staticmethod
        async def update_or_create(**kwargs):
            updates.append(kwargs)

    class FakeAccountQuery:
        def prefetch_related(self, *fields):
            assert fields == ("roles",)
            return self

        def __await__(self):
            async def value():
                return [
                    SimpleNamespace(
                        id="super-user",
                        roles=[
                            SimpleNamespace(
                                id="111111",
                                permissions=["*:*"],
                                tenant_id="default",
                            )
                        ],
                    ),
                    SimpleNamespace(
                        id="tenant-admin-user",
                        roles=[
                            SimpleNamespace(
                                id="tenant-admin",
                                permissions=["*:*"],
                                tenant_id="tenant-1",
                            )
                        ],
                    ),
                    SimpleNamespace(
                        id="normal-user",
                        roles=[
                            SimpleNamespace(
                                permissions=["workflow:r"], tenant_id="tenant-1"
                            )
                        ],
                    ),
                ]

            return value().__await__()

    class FakeAccount:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "id__in": ["super-user", "tenant-admin-user", "normal-user"],
                "deleted": False,
                "type": "user",
            }
            return FakeAccountQuery()

    class FakeRoleQuery:
        async def all(self):
            return [
                SimpleNamespace(id="111111", permissions=["*:*"]),
                SimpleNamespace(id="tenant-admin", permissions=["*:*"]),
                SimpleNamespace(id="normal-role", permissions=["workflow:r"]),
            ]

    class FakeRole:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "id__in": ["111111", "tenant-admin", "normal-role"],
                "deleted": False,
            }
            return FakeRoleQuery()

    class FakeCache:
        async def set(self, key, value, ttl):
            cache_sets.append((key, value, ttl))

    monkeypatch.setattr(workflow_views, "Account", FakeAccount)
    monkeypatch.setattr(workflow_views, "Playbook", FakePlaybook)
    monkeypatch.setattr(workflow_views, "Role", FakeRole)
    monkeypatch.setattr(
        workflow_views, "PlaybookPermissions", FakePlaybookPermissions
    )
    monkeypatch.setattr(workflow_views, "response_ok", lambda data: data)

    request = SimpleNamespace(
        json={
            "playbook_id": "playbook-1",
            "users": {
                "super-user": ["r"],
                "tenant-admin-user": ["r"],
                "normal-user": ["r"],
            },
            "roles": {
                "111111": ["r"],
                "tenant-admin": ["r"],
                "normal-role": ["r", "u"],
            },
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=FakeCache())),
    )

    result = await inspect.unwrap(workflow_views.add_permission)(request)

    assert result == "权限更新成功"
    assert updates == [
        {
            "pb_id": "playbook-1",
            "users_id": "tenant-admin-user",
            "tenant_id": "tenant-1",
            "defaults": {"permissions": ["r"], "roles_id": None},
        },
        {
            "pb_id": "playbook-1",
            "users_id": "normal-user",
            "tenant_id": "tenant-1",
            "defaults": {"permissions": ["r"], "roles_id": None},
        },
        {
            "pb_id": "playbook-1",
            "roles_id": "tenant-admin",
            "tenant_id": "tenant-1",
            "defaults": {"permissions": ["r"], "users_id": None},
        },
        {
            "pb_id": "playbook-1",
            "roles_id": "normal-role",
            "tenant_id": "tenant-1",
            "defaults": {"permissions": ["r", "u"], "users_id": None},
        }
    ]
    assert cache_sets == [
        (("playbook-1", "tenant-admin-user"), ["r"], 2592000),
        (("playbook-1", "normal-user"), ["r"], 2592000),
        (("playbook-1", "tenant-admin"), ["r"], 2592000),
        (("playbook-1", "normal-role"), ["r", "u"], 2592000),
    ]
