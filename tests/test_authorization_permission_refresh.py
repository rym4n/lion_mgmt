# coding=utf-8
import asyncio
from types import SimpleNamespace

import pytest

from libs.error_code.exception import CustomError
from libs.security import authorization


class _FakeAccountQuery:
    def __init__(self, account):
        self.account = account

    def prefetch_related(self, *_args):
        return self

    def __await__(self):
        async def _result():
            return self.account

        return _result().__await__()


class _FakeValuesQuery:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        async def _result():
            return self.value

        return _result().__await__()


class _FakePermissionsMenuQuery:
    def first(self):
        return self

    def values(self, *_args):
        return _FakeValuesQuery({})


class _FakeCacheWithValues(_FakeValuesQuery):
    async def get(self, key):
        return self.value.get(key)


class _FakeCache:
    def __init__(self):
        self.set_calls = []

    async def set(self, key, value, ttl):
        self.set_calls.append((key, value, ttl))


def _account(account_id, permissions, tenant_id="tenant-a"):
    roles = [SimpleNamespace(permissions=permissions, tenant_id=tenant_id)]
    return SimpleNamespace(id=account_id, roles=roles)


def _playbook_account(
    account_id, role_ids, permissions=None, tenant_id="tenant-a"
):
    return SimpleNamespace(
        id=account_id,
        roles=[
            SimpleNamespace(
                id=role_id,
                permissions=permissions or [],
                tenant_id=tenant_id,
            )
            for role_id in role_ids
        ],
    )


class _FakePlaybookPermissionQuery:
    def __init__(self, records):
        self.records = records

    async def all(self):
        return self.records


class _FakePlaybookVersionQuery:
    def __await__(self):
        async def _result():
            return None

        return _result().__await__()


@pytest.mark.parametrize(
    ("role_permissions", "expected"),
    [
        (["workflow:r,uncontrolled"], {"r"}),
        (["workflow:u", "workflow:uncontrolled"], {"u", "e", "p"}),
        (["workflow:d,uncontrolled"], {"d"}),
        (["workflow:r", "workflow:u,uncontrolled"], {"u", "e", "p"}),
        (
            ["workflow:r,u,d", "workflow:uncontrolled"],
            {"r", "u", "d", "e", "p"},
        ),
        (["workflow:*"], set()),
        (["workflow:r"], set()),
    ],
)
def test_uncontrolled_playbook_permissions_respect_module_permissions(
    role_permissions, expected
):
    account = _playbook_account("account-1", ["role-1"], role_permissions)

    assert (
        authorization.uncontrolled_playbook_permissions(account, "tenant-a")
        == expected
    )


def test_uncontrolled_playbook_permissions_ignore_other_tenant_roles():
    account = _playbook_account(
        "account-1",
        ["role-1"],
        ["workflow:r,uncontrolled"],
        tenant_id="tenant-b",
    )

    assert authorization.uncontrolled_playbook_permissions(account, "tenant-a") == set()


def test_check_permissions_refreshes_stale_session_account(monkeypatch):
    stale_account = _account("account-1", ["dashboard:r"])
    fresh_account = _account("account-1", ["asset_management:r"])
    cache = _FakeCache()
    request = SimpleNamespace(
        ctx=SimpleNamespace(
            session={
                "sub": "account-1",
                "account_info": stale_account,
                "tenant_id": "tenant-a",
            }
        ),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=cache)),
        path="/asset-management/asset-model/list",
    )

    monkeypatch.setattr(
        authorization.Account,
        "get_or_none",
        lambda **_kwargs: _FakeAccountQuery(fresh_account),
    )

    assert (
        asyncio.run(
            authorization.check_permissions(request, "asset_management:r")
        )
        is True
    )
    assert request.ctx.session["account_info"] is fresh_account
    assert cache.set_calls[0][0] == "account_account-1"


def test_platform_wildcard_authorizes_nested_asset_permissions():
    account = _account("account-1", ["*:*"])
    request = SimpleNamespace(
        ctx=SimpleNamespace(
            session={
                "sub": "account-1",
                "account_info": account,
                "tenant_id": "tenant-a",
            }
        ),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_FakeCache())),
        path="/asset-management/asset-model/list",
    )

    assert (
        asyncio.run(
            authorization.check_permissions(request, "asset_management:r")
        )
        is True
    )


def test_check_permissions_ignores_roles_from_other_tenants(monkeypatch):
    stale_account = _account("account-1", ["warroom:r"], tenant_id="tenant-b")
    fresh_account = _account("account-1", ["warroom:r"], tenant_id="tenant-b")
    request = SimpleNamespace(
        ctx=SimpleNamespace(
            session={
                "sub": "account-1",
                "account_info": stale_account,
                "tenant_id": "tenant-a",
            }
        ),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_FakeCache())),
        path="/event/detail",
    )

    monkeypatch.setattr(
        authorization.Account,
        "get_or_none",
        lambda **_kwargs: _FakeAccountQuery(fresh_account),
    )
    monkeypatch.setattr(
        authorization.PermissionsMenu,
        "filter",
        lambda **_kwargs: _FakePermissionsMenuQuery(),
    )

    with pytest.raises(CustomError):
        asyncio.run(authorization.check_permissions(request, "warroom:r"))


@pytest.mark.parametrize(
    "role_permission",
    ["agent_report_schedule:c", "agent_report_schedule:c,d,u,r", "*:*"],
)
def test_check_permissions_accepts_canonical_report_schedule_permissions(
    role_permission,
):
    account = _account("account-1", [role_permission])
    request = SimpleNamespace(
        ctx=SimpleNamespace(
            session={
                "sub": "account-1",
                "account_info": account,
                "tenant_id": "tenant-a",
            }
        ),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_FakeCache())),
        path="/agent/report/schedule/save",
    )

    assert (
        asyncio.run(authorization.check_permissions(request, "agent_report_schedule:c"))
        is True
    )


def test_check_playbook_permission_merges_duplicate_user_grants(monkeypatch):
    captured_filters = []

    def fake_filter(**kwargs):
        captured_filters.append(kwargs)
        return _FakePlaybookPermissionQuery(
            [
                SimpleNamespace(permissions=["r"]),
                SimpleNamespace(permissions=["u"]),
            ]
        )

    monkeypatch.setattr(authorization.PlaybookPermissions, "filter", fake_filter)
    monkeypatch.setattr(
        authorization.PlaybookVersion,
        "get_or_none",
        lambda **_kwargs: _FakePlaybookVersionQuery(),
    )

    request = SimpleNamespace(
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_FakeCacheWithValues({}))),
    )

    assert (
        asyncio.run(
            authorization.check_playbook_permission(
                request,
                _playbook_account("account-1", []),
                "playbook-1",
                ("r", "u"),
            )
        )
        is True
    )
    assert captured_filters == [
        {
            "pb_id": "playbook-1",
            "tenant_id": "tenant-a",
            "deleted": False,
            "users_id": "account-1",
        }
    ]


def test_check_playbook_permission_bypasses_records_for_uncontrolled_role(
    monkeypatch,
):
    def unexpected_filter(**_kwargs):
        raise AssertionError("uncontrolled permission must not query playbook grants")

    monkeypatch.setattr(authorization.PlaybookPermissions, "filter", unexpected_filter)
    request = SimpleNamespace(
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_FakeCacheWithValues({}))),
    )
    account = _playbook_account(
        "account-1",
        ["role-1"],
        ["workflow:u,uncontrolled"],
    )

    assert (
        asyncio.run(
            authorization.check_playbook_permission(
                request,
                account,
                "playbook-1",
                ("u", "e", "p"),
            )
        )
        is True
    )


def test_check_playbook_permission_does_not_bypass_missing_module_permission(
    monkeypatch,
):
    monkeypatch.setattr(
        authorization.PlaybookPermissions,
        "filter",
        lambda **_kwargs: _FakePlaybookPermissionQuery([]),
    )
    monkeypatch.setattr(
        authorization.PlaybookVersion,
        "get_or_none",
        lambda **_kwargs: _FakePlaybookVersionQuery(),
    )
    request = SimpleNamespace(
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_FakeCacheWithValues({}))),
    )
    account = _playbook_account(
        "account-1",
        ["role-1"],
        ["workflow:r,uncontrolled"],
    )

    assert (
        asyncio.run(
            authorization.check_playbook_permission(
                request,
                account,
                "playbook-1",
                ("d",),
            )
        )
        is False
    )


def test_check_playbook_permission_merges_duplicate_role_grants(monkeypatch):
    captured_filters = []

    def fake_filter(**kwargs):
        captured_filters.append(kwargs)
        if "users_id" in kwargs:
            return _FakePlaybookPermissionQuery([])
        records_by_role = {
            "role-1": [SimpleNamespace(permissions=["r"])],
            "role-2": [
                SimpleNamespace(permissions=["r"]),
                SimpleNamespace(permissions=["u"]),
            ],
        }
        return _FakePlaybookPermissionQuery(records_by_role.get(kwargs["roles_id"], []))

    monkeypatch.setattr(authorization.PlaybookPermissions, "filter", fake_filter)
    monkeypatch.setattr(
        authorization.PlaybookVersion,
        "get_or_none",
        lambda **_kwargs: _FakePlaybookVersionQuery(),
    )

    request = SimpleNamespace(
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_FakeCacheWithValues({}))),
    )

    assert (
        asyncio.run(
            authorization.check_playbook_permission(
                request,
                _playbook_account("account-1", ["role-2"]),
                "playbook-1",
                ("r", "u"),
            )
        )
        is True
    )
    assert {
        (item.get("users_id"), item.get("roles_id")) for item in captured_filters
    } == {
        ("account-1", None),
        (None, "role-2"),
    }
