# coding=utf-8
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.log_view import views as log_views


def _run_handler(payload):
    request = SimpleNamespace(json=payload)
    handler = log_views.get_logs_by_sql
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    return asyncio.run(handler(request))


def test_logs_query_defaults_to_small_limit(monkeypatch):
    query_vlog = AsyncMock(return_value=[{"message": "ok"}])
    monkeypatch.setattr(log_views, "query_vlog", query_vlog)
    monkeypatch.setattr(log_views, "response_ok", lambda data: {"code": 0, "data": data})

    result = _run_handler({"project_id": 7, "query": "*"})

    assert result == {"code": 0, "data": [{"message": "ok"}]}
    query_vlog.assert_awaited_once_with("*", 7, None, None, limit=log_views.DEFAULT_LOG_QUERY_LIMIT)


def test_logs_query_allows_explicit_limit_with_maximum(monkeypatch):
    query_vlog = AsyncMock(return_value=[])
    monkeypatch.setattr(log_views, "query_vlog", query_vlog)
    monkeypatch.setattr(log_views, "response_ok", lambda data: {"code": 0, "data": data})

    _run_handler({"project_id": 7, "query": "*", "limit": 5000})

    query_vlog.assert_awaited_once_with("*", 7, None, None, limit=log_views.MAX_LOG_QUERY_LIMIT)


def test_logs_query_keeps_limit_pipe_queries_uncompressed(monkeypatch):
    query_vlog = AsyncMock(return_value=[])
    monkeypatch.setattr(log_views, "query_vlog", query_vlog)
    monkeypatch.setattr(log_views, "response_ok", lambda data: {"code": 0, "data": data})

    _run_handler({"project_id": 7, "query": '* | limit 500'})

    query_vlog.assert_awaited_once_with("* | limit 500", 7, None, None, limit=log_views.MAX_LOG_QUERY_LIMIT)


def test_logs_query_parses_utc_time_range(monkeypatch):
    query_vlog = AsyncMock(return_value=[])
    monkeypatch.setattr(log_views, "query_vlog", query_vlog)
    monkeypatch.setattr(log_views, "response_ok", lambda data: {"code": 0, "data": data})

    _run_handler(
        {
            "project_id": 7,
            "query": "*",
            "start": "2026-07-21T00:00:00Z",
            "end": "2026-07-21T01:00:00Z",
            "limit": 20,
        }
    )

    query_vlog.assert_awaited_once_with(
        "*",
        7,
        "2026-07-21T00:00:00Z",
        "2026-07-21T01:00:00Z",
        limit=20,
    )
