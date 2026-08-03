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


def _request(project_id=7, tenant_id="tenant-a"):
    return SimpleNamespace(
        json={"project_id": project_id},
        ctx=SimpleNamespace(session={"tenant_id": tenant_id}),
    )


def _handler():
    handler = log_views.delete_project_logs
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


def test_delete_endpoint_requires_log_delete_permission():
    source = Path(log_views.__file__).read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "delete_project_logs"
    )
    permission = next(
        decorator
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and getattr(decorator.func, "id", None) == "requires_permission"
    )
    assert permission.args[0].value == "logs:d"


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
