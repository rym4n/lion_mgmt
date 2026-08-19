# coding=utf-8
import asyncio
from types import SimpleNamespace

import pytest

from services.event_ingestion import views as event_views


class _ValuesQuery:
    def __init__(self, rows):
        self._rows = rows

    async def values(self, *fields):
        return self._rows


def _request(tenant_id="tenant-a"):
    return SimpleNamespace(
        json={},
        ctx=SimpleNamespace(session={"tenant_id": tenant_id}),
    )


def _handler():
    return event_views.get_event_ingestion_retention_list.__wrapped__


def test_retention_list_returns_project_retention_rows(monkeypatch):
    captured = {}

    def fake_filter(**kwargs):
        captured.update(kwargs)
        return _ValuesQuery(
            [
                {"project_id": 3, "event_ingestion_name": "syslog", "retention_days": 14},
                {"project_id": 5, "event_ingestion_name": "kafka", "retention_days": None},
            ]
        )

    monkeypatch.setattr(event_views.EventsIngestion, "filter", fake_filter)

    response = asyncio.run(_handler()(_request()))

    assert response.status == 200
    body = response.body.decode()
    assert '"project_id":3' in body
    assert '"retention_days":14' in body
    # retention_days 为 NULL 时回落到默认 7
    assert '"retention_days":7' in body


def test_retention_list_filters_by_tenant_and_excludes_deleted(monkeypatch):
    captured = {}

    def fake_filter(**kwargs):
        captured.update(kwargs)
        return _ValuesQuery([])

    monkeypatch.setattr(event_views.EventsIngestion, "filter", fake_filter)

    asyncio.run(_handler()(_request(tenant_id="tenant-b")))

    assert captured["tenant_id"] == "tenant-b"
    assert captured["deleted"] is False
    assert captured["project_id__isnull"] is False
