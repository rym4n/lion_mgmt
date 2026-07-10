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


class _FakeCache:
    def __init__(self):
        self.set_calls = []

    async def set(self, key, value, ttl):
        self.set_calls.append((key, value, ttl))


def _account(account_id, permissions, tenant_id="tenant-a"):
    roles = [SimpleNamespace(permissions=permissions, tenant_id=tenant_id)]
    return SimpleNamespace(id=account_id, roles=roles)


def test_check_permissions_refreshes_stale_session_account(monkeypatch):
    stale_account = _account("account-1", ["dashboard:r"])
    fresh_account = _account("account-1", ["supply_chain_review:model:r"])
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
        path="/supply-chain-review/ci-type/list",
    )

    monkeypatch.setattr(
        authorization.Account,
        "get_or_none",
        lambda **_kwargs: _FakeAccountQuery(fresh_account),
    )

    assert asyncio.run(authorization.check_permissions(request, "supply_chain_review:model:r")) is True
    assert request.ctx.session["account_info"] is fresh_account
    assert cache.set_calls[0][0] == "account_account-1"


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
