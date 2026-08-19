# coding=utf-8
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import orjson
import pytest

from libs.error_code.exception import CustomError
from services.log_view import views as log_views


class _ValuesQuery:
    def __init__(self, rows):
        self._rows = rows

    async def values(self, *fields):
        return self._rows


def _request(tenant_id="tenant-a", **body):
    return SimpleNamespace(
        json=body,
        ctx=SimpleNamespace(session={"tenant_id": tenant_id}),
    )


def _handler():
    handler = log_views.cleanup_expired_logs
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    return handler


def _rows():
    return [
        {"project_id": 3, "event_ingestion_name": "syslog", "retention_days": 14},
        {"project_id": 5, "event_ingestion_name": "kafka", "retention_days": 0},
        {"project_id": 9, "event_ingestion_name": "http", "retention_days": None},
    ]


def test_cleanup_uses_retention_days_to_build_time_filter(monkeypatch):
    calls = []

    monkeypatch.setattr(
        log_views.EventsIngestion, "filter", lambda **_kw: _ValuesQuery(_rows())
    )

    async def fake_delete_vlog(project_id, account_id=0, log_filter="*"):
        calls.append((project_id, log_filter))
        return {"task_id": f"task-{project_id}"}

    monkeypatch.setattr(log_views, "delete_vlog", fake_delete_vlog)

    response = asyncio.run(_handler()(_request()))
    payload = orjson.loads(response.body)["data"]

    # retention_days=0 表示不清理，计入 skipped
    assert [item["project_id"] for item in payload["cleaned"]] == [3, 9]
    assert [item["project_id"] for item in payload["skipped"]] == [5]

    # 每次调用都带 _time:< 前缀的过滤器
    assert [pid for pid, _ in calls] == [3, 9]
    for _pid, log_filter in calls:
        assert log_filter.startswith("_time:<")
        assert log_filter.endswith("Z")

    # project 9 的 retention_days 为 NULL，按默认 7 天算，cutoff 应晚于 project 3 的 14 天
    cutoff_by_project = {pid: f for pid, f in calls}
    assert cutoff_by_project[9] > cutoff_by_project[3]


def test_cleanup_restricts_to_requested_projects_within_tenant(monkeypatch):
    captured = {}

    def fake_filter(**kwargs):
        captured.update(kwargs)
        return _ValuesQuery([_rows()[0]])

    monkeypatch.setattr(log_views.EventsIngestion, "filter", fake_filter)

    async def fake_delete_vlog(project_id, account_id=0, log_filter="*"):
        return {"task_id": "t"}

    monkeypatch.setattr(log_views, "delete_vlog", fake_delete_vlog)

    asyncio.run(_handler()(_request(tenant_id="tenant-b", project_ids=[3])))

    assert captured["tenant_id"] == "tenant-b"
    assert captured["deleted"] is False
    assert captured["project_id__in"] == [3]


def test_cleanup_records_failure_without_aborting_remaining_projects(monkeypatch):
    monkeypatch.setattr(
        log_views.EventsIngestion, "filter", lambda **_kw: _ValuesQuery(_rows())
    )

    async def fake_delete_vlog(project_id, account_id=0, log_filter="*"):
        if project_id == 3:
            return None
        return {"task_id": f"task-{project_id}"}

    monkeypatch.setattr(log_views, "delete_vlog", fake_delete_vlog)

    response = asyncio.run(_handler()(_request()))
    payload = orjson.loads(response.body)["data"]

    # project 3 失败被记入 skipped，project 9 仍然继续处理
    assert [item["project_id"] for item in payload["cleaned"]] == [9]
    assert {item["project_id"] for item in payload["skipped"]} == {3, 5}


def test_cleanup_rejects_non_integer_project_ids(monkeypatch):
    monkeypatch.setattr(
        log_views.EventsIngestion, "filter", lambda **_kw: _ValuesQuery([])
    )

    with pytest.raises(CustomError):
        asyncio.run(_handler()(_request(project_ids=["3; DROP TABLE"])))


def test_cleanup_requires_log_delete_permission():
    import ast
    from pathlib import Path

    source = Path(log_views.__file__.replace(".pyc", ".py")).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "cleanup_expired_logs":
            permission = next(
                decorator
                for decorator in node.decorator_list
                if isinstance(decorator, ast.Call)
                and getattr(decorator.func, "id", None) == "requires_permission"
            )
            assert permission.args[0].value == "logs:d"
            return
    raise AssertionError("cleanup_expired_logs not found")
