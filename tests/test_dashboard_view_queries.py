# coding=utf-8
import asyncio
from datetime import date
import importlib
import inspect
from types import SimpleNamespace

import orjson
import pytest

from services.dashboard.views import (
    _DASHBOARD_MATERIALIZED_VIEWS,
    _DASHBOARD_REFRESH_CACHE_KEY,
    _DASHBOARD_REFRESH_CACHE_TTL_SECONDS,
    _DASHBOARD_REFRESH_TASK_NAME,
    _refresh_dashboard_materialized_views,
    _schedule_dashboard_materialized_view_refresh,
    view_dashboard,
)


def _compact_sql(sql: str) -> str:
    return " ".join(sql.lower().replace("public.", "").split()).replace(" ,", ",")


CASES = {
    "events_day": {
        "rows": [{"day": "2026-07-11", "total": 3}],
        "expected": [{"day": "2026-07-11", "counts": 3}],
        "sql_parts": ("FROM events_day", "day >= $1", "day <= $2", "tenant_id = $3"),
    },
    "events_status": {
        "rows": [{"event_process_status": "closed", "sum": 2}],
        "expected": [{"event_process_status": "closed", "counts": 2}],
        "sql_parts": ("FROM events_status", "day >= $1", "day <= $2", "tenant_id = $3"),
    },
    "runlog_playbook": {
        "rows": [{"playbook_name": "inspection", "sum": 4}],
        "expected": [{"playbook_name": "inspection", "counts": 4}],
        "sql_parts": (
            "FROM runlog_playbook",
            "day >= $1",
            "day <= $2",
            "tenant_id = $3",
        ),
    },
    "runlog_action": {
        "rows": [{"action_display_name": "check port", "sum": 5}],
        "expected": [{"action_display_name": "check port", "counts": 5}],
        "sql_parts": ("FROM runlog_action", "day >= $1", "day <= $2", "tenant_id = $3"),
    },
}


class FakeScheduledTask:
    def __init__(self, coroutine):
        self.coroutine = coroutine

    def done(self):
        return False

    def close(self):
        self.coroutine.close()


class FakeApp:
    def __init__(self, cache_client=None):
        self.ctx = SimpleNamespace(cache_client=cache_client)
        self.tasks = {}

    def get_task(self, name, *, raise_exception=False):
        return self.tasks.get(name)

    def add_task(self, coroutine, *, name):
        self.tasks[name] = FakeScheduledTask(coroutine)

    def close_tasks(self):
        for task in self.tasks.values():
            task.close()


class FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


@pytest.mark.parametrize("static_type", CASES)
def test_dashboard_returns_snapshot_without_synchronous_refresh(
    monkeypatch, static_type
):
    case = CASES[static_type]
    captured = {"queries": []}

    class FakeConnection:
        async def execute_query_dict(self, sql, params):
            captured["queries"].append((sql, params))
            return case["rows"]

        async def execute_query(self, *_args, **_kwargs):
            raise AssertionError(
                "dashboard requests must not refresh materialized views"
            )

    app = FakeApp()
    request = SimpleNamespace(
        json={
            "static_type": static_type,
            "start_time": "2026-06-11T00:00:00Z",
            "end_time": "2026-07-11T23:59:59Z",
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
        app=app,
    )

    monkeypatch.setattr(
        "services.dashboard.views.in_transaction",
        lambda: FakeTransaction(FakeConnection()),
    )
    monkeypatch.setattr("services.dashboard.views.response_ok", lambda data: data)

    try:
        result = asyncio.run(inspect.unwrap(view_dashboard)(request))
    finally:
        app.close_tasks()

    assert result == case["expected"]
    assert len(captured["queries"]) == 1
    sql, params = captured["queries"][0]
    assert "REFRESH MATERIALIZED VIEW" not in inspect.getsource(
        inspect.unwrap(view_dashboard)
    )
    assert all(sql_part in sql for sql_part in case["sql_parts"])
    assert params[0].isoformat() == "2026-06-11T00:00:00+00:00"
    assert params[1].isoformat() == "2026-07-11T23:59:59+00:00"
    assert params[2] == "tenant-1"
    assert set(app.tasks) == {_DASHBOARD_REFRESH_TASK_NAME}


def test_dashboard_response_serializes_database_date(monkeypatch):
    class FakeConnection:
        async def execute_query_dict(self, _sql, _params):
            return [{"day": date(2026, 7, 11), "total": 3}]

    app = FakeApp()
    request = SimpleNamespace(
        json={
            "static_type": "events_day",
            "start_time": "2026-06-11T00:00:00Z",
            "end_time": "2026-07-11T23:59:59Z",
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
        app=app,
    )
    monkeypatch.setattr(
        "services.dashboard.views.in_transaction",
        lambda: FakeTransaction(FakeConnection()),
    )

    try:
        response = asyncio.run(inspect.unwrap(view_dashboard)(request))
    finally:
        app.close_tasks()

    assert response.status == 200
    assert orjson.loads(response.body) == {
        "code": 0,
        "data": [{"day": "2026-07-11", "counts": 3}],
    }


def test_background_refresh_is_deduplicated_and_concurrent(monkeypatch):
    captured = {
        "cache_add": None,
        "lock_query": None,
        "settings": [],
        "refresh_queries": [],
    }

    class FakeCache:
        async def add(self, key, value, ttl):
            captured["cache_add"] = (key, value, ttl)

    class FakeConnection:
        async def execute_query_dict(self, sql, params):
            captured["lock_query"] = (sql, params)
            return [{"acquired": True}]

        async def execute_query(self, sql):
            if sql.startswith("SET LOCAL"):
                captured["settings"].append(sql)
            else:
                captured["refresh_queries"].append(sql)

    monkeypatch.setattr(
        "services.dashboard.views.in_transaction",
        lambda: FakeTransaction(FakeConnection()),
    )

    asyncio.run(_refresh_dashboard_materialized_views(FakeApp(FakeCache())))

    assert captured["cache_add"] == (
        _DASHBOARD_REFRESH_CACHE_KEY,
        "1",
        _DASHBOARD_REFRESH_CACHE_TTL_SECONDS,
    )
    assert (
        "pg_try_advisory_xact_lock(hashtextextended($1, 0))"
        in captured["lock_query"][0]
    )
    assert captured["settings"] == [
        "SET LOCAL lock_timeout = '5s'",
        "SET LOCAL statement_timeout = '120s'",
    ]
    assert captured["refresh_queries"] == [
        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view_name}"
        for view_name in _DASHBOARD_MATERIALIZED_VIEWS
    ]


def test_background_refresh_skips_when_throttle_key_exists(monkeypatch):
    class BusyCache:
        async def add(self, _key, _value, ttl):
            raise ValueError("already refreshing")

    def fail_if_transaction_starts():
        raise AssertionError("a throttled refresh must not open a transaction")

    monkeypatch.setattr(
        "services.dashboard.views.in_transaction", fail_if_transaction_starts
    )

    asyncio.run(_refresh_dashboard_materialized_views(FakeApp(BusyCache())))


def test_background_refresh_keeps_throttle_when_cancelled(monkeypatch):
    class FakeCache:
        async def add(self, _key, _value, ttl):
            return True

        async def delete(self, _key):
            raise AssertionError("cancellation must not delete an unowned lock")

    class CancellingConnection:
        async def execute_query_dict(self, _sql, _params):
            return [{"acquired": True}]

        async def execute_query(self, sql):
            if sql.startswith("REFRESH MATERIALIZED VIEW"):
                raise asyncio.CancelledError

    monkeypatch.setattr(
        "services.dashboard.views.in_transaction",
        lambda: FakeTransaction(CancellingConnection()),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_refresh_dashboard_materialized_views(FakeApp(FakeCache())))


def test_schedule_keeps_one_managed_refresh_task():
    app = FakeApp()

    try:
        _schedule_dashboard_materialized_view_refresh(app)
        first_task = app.tasks[_DASHBOARD_REFRESH_TASK_NAME]
        _schedule_dashboard_materialized_view_refresh(app)
    finally:
        app.close_tasks()

    assert app.tasks[_DASHBOARD_REFRESH_TASK_NAME] is first_task


def test_dashboard_refresh_index_migration_supports_concurrent_refresh():
    migration = importlib.import_module(
        "migrations.models.69_20260711211500_enable_concurrent_dashboard_refresh"
    )
    migration_sql = asyncio.run(migration.upgrade(None))
    downgrade_sql = asyncio.run(migration.downgrade(None))

    expected_indexes = {
        "idx_events_day_refresh_unique": "public.events_day (tenant_id, day)",
        "idx_events_status_refresh_unique": (
            "public.events_status (tenant_id, day, event_process_status)"
        ),
        "idx_runlog_playbook_refresh_unique": (
            "public.runlog_playbook (tenant_id, day, playbook_name)"
        ),
        "idx_runlog_action_refresh_unique": (
            "public.runlog_action (tenant_id, day, action_display_name)"
        ),
    }

    events_day_view = "CREATE MATERIALIZED VIEW IF NOT EXISTS public.events_day"
    events_status_view = "CREATE MATERIALIZED VIEW IF NOT EXISTS public.events_status"
    assert events_day_view in migration_sql
    assert events_status_view in migration_sql
    expected_event_views = (
        "create materialized view if not exists events_day as "
        "select (ctime::date) as day, count(id) as total, tenant_id "
        "from events group by tenant_id, day;",
        "create materialized view if not exists events_status as "
        "select (ctime::date) as day, event_process_status, count(id) as total, "
        "tenant_id from events group by tenant_id, day, event_process_status;",
    )
    compact_sql = _compact_sql(migration_sql)
    for expected_view in expected_event_views:
        assert expected_view in compact_sql
    assert migration_sql.index(events_day_view) < migration_sql.index(
        "idx_events_day_refresh_unique"
    )
    assert migration_sql.index(events_status_view) < migration_sql.index(
        "idx_events_status_refresh_unique"
    )
    assert migration_sql.count("CREATE UNIQUE INDEX") == 4
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" not in migration_sql
    for index_name, index_target in expected_indexes.items():
        assert index_name in migration_sql
        assert f"ON {index_target}" in migration_sql
        assert f"DROP INDEX IF EXISTS public.{index_name}" in downgrade_sql
