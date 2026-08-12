# coding=utf-8
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from libs import utils
from libs.error_code.exception import CustomError
from services.log_view import views as log_views


class _ProjectQuery:
    def __init__(self, exists):
        self._exists = exists

    async def exists(self):
        return self._exists


def _request(project_id=7, tenant_id="tenant-a", **params):
    return SimpleNamespace(
        json={"project_id": project_id, **params},
        ctx=SimpleNamespace(session={"tenant_id": tenant_id}),
    )


def _handler(handler=log_views.delete_project_logs):
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    return handler


def test_delete_logs_accepts_owned_soft_deleted_source(monkeypatch):
    filters = {}

    def fake_filter(**kwargs):
        filters.update(kwargs)
        return _ProjectQuery(True)

    delete_vlog = AsyncMock(return_value={"task_id": "task-1"})
    monkeypatch.setattr(log_views.EventsIngestion, "filter", fake_filter)
    monkeypatch.setattr(log_views, "delete_vlog", delete_vlog)
    monkeypatch.setattr(log_views, "response_ok", lambda data: {"code": 0, "data": data})

    result = asyncio.run(_handler()(_request()))

    assert filters == {"project_id": 7, "tenant_id": "tenant-a"}
    delete_vlog.assert_awaited_once_with(7)
    assert result == {"code": 0, "data": {"task_id": "task-1"}}


def test_delete_logs_rejects_project_from_another_tenant(monkeypatch):
    delete_vlog = AsyncMock()
    monkeypatch.setattr(
        log_views.EventsIngestion,
        "filter",
        lambda **_kwargs: _ProjectQuery(False),
    )
    monkeypatch.setattr(log_views, "delete_vlog", delete_vlog)

    with pytest.raises(CustomError):
        asyncio.run(_handler()(_request()))

    delete_vlog.assert_not_awaited()


@pytest.mark.parametrize(
    "function_name",
    ["delete_project_logs", "get_delete_project_logs_status"],
)
def test_delete_endpoints_require_log_delete_permission(function_name):
    source = Path(log_views.__file__).read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name
    )
    permission = next(
        decorator
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and getattr(decorator.func, "id", None) == "requires_permission"
    )
    assert permission.args[0].value == "logs:d"


def test_log_query_accepts_histogram_millisecond_iso_range(monkeypatch):
    query_vlog = AsyncMock(return_value=[])
    monkeypatch.setattr(log_views, "query_vlog", query_vlog)
    monkeypatch.setattr(log_views, "response_ok", lambda data: {"code": 0, "data": data})

    request = _request(
        project_id=13,
        query="*",
        start="2026-06-30T00:00:00.000Z",
        end="2026-06-30T23:59:59.999Z",
    )
    result = asyncio.run(_handler(log_views.get_logs_by_sql)(request))

    assert result == {"code": 0, "data": []}
    query_vlog.assert_awaited_once_with(
        "*",
        13,
        "2026-06-30T00:00:00Z",
        "2026-06-30T23:59:59.999000Z",
        limit=100,
    )


class _FakeHttpClient:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_delete_vlog_uses_project_tenant_headers_and_full_project_filter(monkeypatch):
    calls = []
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://victoria-logs/delete/run_task"),
        json={"task_id": "12345"},
    )
    monkeypatch.setattr(
        utils.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeHttpClient(response, calls),
    )

    result = asyncio.run(utils.delete_vlog(project_id=13))

    assert result == {"task_id": "12345"}
    assert calls == [
        (
            f"{utils.VL_HOST}/delete/run_task",
            {
                "headers": {"AccountID": "0", "ProjectID": "13"},
                "params": {"filter": "*"},
            },
        )
    ]


def test_get_delete_status_matches_task_and_project_tenant(monkeypatch):
    calls = []
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "http://victoria-logs/delete/active_tasks"),
        json=[
            {
                "task_id": "12345",
                "tenant_ids": [{"account_id": 0, "project_id": 13}],
                "filter": "*",
            }
        ],
    )
    monkeypatch.setattr(
        utils.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeHttpClient(response, calls),
    )

    active = asyncio.run(
        utils.get_vlog_delete_task_status(task_id="12345", project_id=13)
    )

    assert active is True
    assert calls == [(f"{utils.VL_HOST}/delete/active_tasks", {})]


def test_get_delete_status_reports_completed_when_task_is_no_longer_active(monkeypatch):
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "http://victoria-logs/delete/active_tasks"),
        json=[],
    )
    monkeypatch.setattr(
        utils.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeHttpClient(response, []),
    )

    active = asyncio.run(
        utils.get_vlog_delete_task_status(task_id="12345", project_id=13)
    )

    assert active is False


def test_delete_status_endpoint_checks_ownership_and_returns_completion(monkeypatch):
    filters = {}

    def fake_filter(**kwargs):
        filters.update(kwargs)
        return _ProjectQuery(True)

    get_status = AsyncMock(return_value=False)
    monkeypatch.setattr(log_views.EventsIngestion, "filter", fake_filter)
    monkeypatch.setattr(log_views, "get_vlog_delete_task_status", get_status)
    monkeypatch.setattr(log_views, "response_ok", lambda data: {"code": 0, "data": data})

    result = asyncio.run(
        _handler(log_views.get_delete_project_logs_status)(
            _request(project_id=13, task_id="12345")
        )
    )

    assert filters == {"project_id": 13, "tenant_id": "tenant-a"}
    get_status.assert_awaited_once_with("12345", 13)
    assert result == {
        "code": 0,
        "data": {"task_id": "12345", "active": False, "status": "completed"},
    }
