# coding=utf-8
import inspect
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from core.maintenance import DEFAULT_TENANT_ID
from libs.error_code.exception import CustomError
from services.inspection import views


def _request(tenant_id):
    return SimpleNamespace(
        json={},
        ctx=SimpleNamespace(session={"tenant_id": tenant_id}),
    )


def test_resolve_authenticated_tenant_rejects_forged_body_tenant():
    request = _request("tenant-a")

    with pytest.raises(CustomError) as exc_info:
        views._resolve_authenticated_tenant_id(
            request,
            {"tenant_id": "tenant-b"},
        )

    assert exc_info.value.enum is ErrorCode.FORBIDDEN


def test_resolve_authenticated_tenant_accepts_omitted_or_matching_body_tenant():
    request = _request("tenant-a")

    assert views._resolve_authenticated_tenant_id(request, {}) == "tenant-a"
    assert (
        views._resolve_authenticated_tenant_id(
            request,
            {"tenant_id": "tenant-a"},
        )
        == "tenant-a"
    )


@pytest.mark.asyncio
async def test_add_result_rejects_forged_body_tenant_before_processing():
    request = _request("tenant-a")
    request.json = {
        "tenant_id": "tenant-b",
        "result_data": [{"target_id": "target-1", "result": {"ok": True}}],
        "start_time": "2026-07-29T00:00:00+08:00",
    }

    with pytest.raises(CustomError) as exc_info:
        await inspect.unwrap(views.add_result)(request)

    assert exc_info.value.enum is ErrorCode.FORBIDDEN


@pytest.mark.asyncio
async def test_get_all_configs_uses_authenticated_tenant(monkeypatch):
    captured = {}

    async def fake_get_all_configs(tenant_id, schedule_ids, target_ids):
        captured.update(
            tenant_id=tenant_id,
            schedule_ids=schedule_ids,
            target_ids=target_ids,
        )
        return []

    monkeypatch.setattr(views.logic, "get_all_configs", fake_get_all_configs)
    request = _request("tenant-a")
    request.json = {
        "schedule_id": "schedule-1",
        "target_id": "target-1",
        "tenant_id": "tenant-a",
    }

    await inspect.unwrap(views.get_all_configs)(request)

    assert captured == {
        "tenant_id": "tenant-a",
        "schedule_ids": "schedule-1",
        "target_ids": "target-1",
    }


class _FirstQuery:
    def __init__(self, result, filters):
        self.result = result
        self.filters = filters
        self.ordering = None

    def order_by(self, *fields):
        self.ordering = fields
        return self

    async def first(self):
        return self.result


@pytest.mark.asyncio
async def test_automation_playbook_lookup_is_fixed_to_default_tenant(monkeypatch):
    playbook = SimpleNamespace(id="playbook-default")
    version = SimpleNamespace(id="version-default")
    captured = {}

    class FakePlaybook:
        @staticmethod
        def filter(**filters):
            captured["playbook"] = filters
            return _FirstQuery(playbook, filters)

    class FakePlaybookVersion:
        @staticmethod
        def filter(**filters):
            query = _FirstQuery(version, filters)
            captured["version"] = query
            return query

    monkeypatch.setattr(views, "Playbook", FakePlaybook)
    monkeypatch.setattr(views, "PlaybookVersion", FakePlaybookVersion)

    result = await views._get_automation_inspection_playbook()

    assert result == (playbook, version)
    assert captured["playbook"] == {
        "name": "自动化巡检",
        "deleted": False,
        "tenant_id": DEFAULT_TENANT_ID,
        "internal": True,
    }
    assert captured["version"].filters == {
        "playbook_id": "playbook-default",
        "tenant_id": DEFAULT_TENANT_ID,
        "deleted": False,
        "status": 1,
    }
    assert captured["version"].ordering == ("-utime",)
