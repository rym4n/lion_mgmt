# coding=utf-8
from types import SimpleNamespace

import pytest

from services.user import views as user_views


class _FakeQuery:
    def __init__(self):
        self.filters = []

    def distinct(self):
        return self

    def filter(self, *args, **kwargs):
        self.filters.append((args, kwargs))
        return self

    async def count(self):
        return 0

    def offset(self, _offset):
        return self

    def limit(self, _limit):
        return self

    def all(self):
        return self

    async def prefetch_related(self, *_names):
        return []


def _unwrap_permission_handler(handler):
    return handler.__wrapped__.__wrapped__


@pytest.mark.asyncio
async def test_all_users_filters_role_keyword_by_active_tenant_role(monkeypatch):
    query = _FakeQuery()

    class FakeAccount:
        @staticmethod
        def filter(*args, **kwargs):
            query.filters.append((args, kwargs))
            return query

    captured = {}

    def fake_response_ok(payload):
        captured["payload"] = payload
        return payload

    monkeypatch.setattr(user_views, "Account", FakeAccount)
    monkeypatch.setattr(user_views, "response_ok", fake_response_ok)

    request = SimpleNamespace(
        json={"roles": "test"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    await _unwrap_permission_handler(user_views.all_users)(request)

    assert (
        (),
        {
            "roles__name__icontains": "test",
            "roles__tenant_id__in": ["tenant-a"],
            "roles__deleted": False,
        },
    ) in query.filters
    assert captured["payload"]["total"] == 0


@pytest.mark.asyncio
async def test_all_users_filters_multiple_roles_inside_current_tenant(monkeypatch):
    query = _FakeQuery()

    class FakeAccount:
        @staticmethod
        def filter(*args, **kwargs):
            query.filters.append((args, kwargs))
            return query

    monkeypatch.setattr(user_views, "Account", FakeAccount)
    monkeypatch.setattr(user_views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={"roles": ["空间管理员", "系统管理员"]},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    await _unwrap_permission_handler(user_views.all_users)(request)

    assert (
        (),
        {
            "roles__name__in": ["空间管理员", "系统管理员"],
            "roles__tenant_id__in": ["tenant-a"],
            "roles__deleted": False,
        },
    ) in query.filters


@pytest.mark.asyncio
async def test_api_users_use_same_keyword_tenant_role_filter(monkeypatch):
    query = _FakeQuery()

    class FakeAccount:
        @staticmethod
        def filter(*args, **kwargs):
            query.filters.append((args, kwargs))
            return query

    monkeypatch.setattr(user_views, "Account", FakeAccount)
    monkeypatch.setattr(user_views, "response_ok", lambda payload: payload)

    request = SimpleNamespace(
        json={"roles": "系统管理员"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    await _unwrap_permission_handler(user_views.list_api_users)(request)

    assert (
        (),
        {
            "roles__name__icontains": "系统管理员",
            "roles__tenant_id__in": ["tenant-a"],
            "roles__deleted": False,
        },
    ) in query.filters
