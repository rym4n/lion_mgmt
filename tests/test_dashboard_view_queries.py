# coding=utf-8
import asyncio
from datetime import date
from decimal import Decimal
import importlib
import inspect
from types import SimpleNamespace

import orjson
import pytest

from services.dashboard import views as dashboard_views
from services.dashboard import materialized_refresh
from services.dashboard.views import (
    count_apps,
    count_playbooks,
    playbooks_success_rate,
    view_dashboard,
)
from services.dashboard.materialized_refresh import (
    _DASHBOARD_MATERIALIZED_VIEWS,
    _DASHBOARD_REFRESH_CACHE_KEY,
    _DASHBOARD_REFRESH_CACHE_TTL_SECONDS,
    refresh_dashboard_materialized_views,
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


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], 0),
        ([{"counts": None}], 0),
        ([{"counts": 3}], 3),
    ],
)
def test_dashboard_app_count_aggregate_handles_empty_scope(
    monkeypatch,
    rows,
    expected,
):
    captured = {}

    class FakeAggregateQuery:
        def annotate(self, **annotations):
            captured["annotations"] = annotations
            return self

        async def values(self, *fields):
            captured["value_fields"] = fields
            return rows

    def filter_rows(**filters):
        captured["filters"] = filters
        return FakeAggregateQuery()

    async def get_mode(session_id):
        assert session_id == "session-1"
        return False

    request = SimpleNamespace(
        ctx=SimpleNamespace(session={"id": "session-1", "tenant_id": "tenant-1"}),
        app=SimpleNamespace(
            ctx=SimpleNamespace(
                cache_client=SimpleNamespace(get=get_mode),
            )
        ),
    )
    monkeypatch.setattr(
        dashboard_views,
        "ToolResources",
        SimpleNamespace(filter=filter_rows),
    )
    monkeypatch.setattr(dashboard_views, "response_ok", lambda data: data)

    result = asyncio.run(inspect.unwrap(count_apps)(request))

    assert result == expected
    assert captured["filters"] == {
        "deleted": False,
        "internal": False,
        "tenant_id": "tenant-1",
    }
    assert set(captured["annotations"]) == {"counts"}
    assert captured["value_fields"] == ("counts",)


@pytest.mark.parametrize(
    ("maintenance_mode", "aggregate_rows", "expected"),
    [(False, [], 0), (False, [{"counts": 2}], 2), (True, [{"counts": 3}], 3)],
)
def test_count_playbooks_counts_distinct_playbooks_with_published_versions(
    monkeypatch, maintenance_mode, aggregate_rows, expected
):
    captured = {}

    class FakeVersionAggregateQuery:
        def annotate(self, **annotations):
            captured["annotations"] = annotations
            return self

        def group_by(self, *fields):
            captured["group_fields"] = fields
            return self

        async def values(self, *fields):
            captured["value_fields"] = fields
            return aggregate_rows

    def filter_versions(**filters):
        captured["filters"] = filters
        return FakeVersionAggregateQuery()

    class FakePlaybookQuery:
        def distinct(self):
            return self

        async def count(self):
            return 99

    def filter_playbooks(**_filters):
        return FakePlaybookQuery()

    async def get_mode(session_id):
        assert session_id == "session-1"
        return maintenance_mode

    request = SimpleNamespace(
        ctx=SimpleNamespace(session={"id": "session-1", "tenant_id": "tenant-1"}),
        app=SimpleNamespace(
            ctx=SimpleNamespace(cache_client=SimpleNamespace(get=get_mode))
        ),
    )
    monkeypatch.setattr(
        dashboard_views,
        "PlaybookVersion",
        SimpleNamespace(filter=filter_versions),
        raising=False,
    )
    monkeypatch.setattr(
        dashboard_views,
        "Playbook",
        SimpleNamespace(filter=filter_playbooks),
        raising=False,
    )
    monkeypatch.setattr(dashboard_views, "response_ok", lambda data: data)

    result = asyncio.run(inspect.unwrap(count_playbooks)(request))

    assert result == expected
    assert captured["filters"] == {
        "status": 1,
        "deleted": False,
        "tenant_id": "tenant-1",
        "playbook__deleted": False,
        "playbook__internal": maintenance_mode,
        "playbook__tenant_id": "tenant-1",
    }
    assert set(captured["annotations"]) == {"counts"}
    count_expression = captured["annotations"]["counts"]
    assert count_expression.field == "playbook_id"
    assert count_expression.distinct is True
    assert captured["group_fields"] == ("tenant_id",)
    assert captured["value_fields"] == ("counts",)


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
    assert set(app.tasks) == set()


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


def test_success_rate_returns_materialized_snapshot(monkeypatch):
    captured = {}

    class FakeConnection:
        async def execute_query_dict(self, sql, params):
            captured["query"] = (sql, params)
            return [{"playbook_name": "inspection", "success_rate": Decimal("0.75")}]

    app = FakeApp()
    request = SimpleNamespace(
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
        app=app,
    )
    monkeypatch.setattr(
        "services.dashboard.views.in_transaction",
        lambda: FakeTransaction(FakeConnection()),
    )
    monkeypatch.setattr("services.dashboard.views.response_ok", lambda data: data)

    try:
        result = asyncio.run(inspect.unwrap(playbooks_success_rate)(request))
    finally:
        app.close_tasks()

    sql, params = captured["query"]
    assert result == [{"playbook_name": "inspection", "success_rate": 0.75}]
    assert "FROM runlog_playbook_success_rate" in sql
    assert "FROM runlog\n" not in sql
    assert params == ("tenant-1",)
    assert set(app.tasks) == set()


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
        materialized_refresh,
        "in_transaction",
        lambda: FakeTransaction(FakeConnection()),
    )

    asyncio.run(refresh_dashboard_materialized_views(FakeApp(FakeCache())))

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
        materialized_refresh, "in_transaction", fail_if_transaction_starts
    )

    asyncio.run(refresh_dashboard_materialized_views(FakeApp(BusyCache())))


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
        materialized_refresh,
        "in_transaction",
        lambda: FakeTransaction(CancellingConnection()),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(refresh_dashboard_materialized_views(FakeApp(FakeCache())))


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


def test_success_rate_materialized_view_preserves_query_contract():
    migration = importlib.import_module(
        "migrations.models.110_20260819001000_materialize_dashboard_success_rate"
    )
    migration_sql = asyncio.run(migration.upgrade(None))
    downgrade_sql = asyncio.run(migration.downgrade(None))
    compact_sql = _compact_sql(migration_sql)

    assert "create materialized view runlog_playbook_success_rate as" in compact_sql
    assert "max(case when status != 0 then 1 else 0 end) as has_fail" in compact_sql
    assert "where playbook_name != '执行动作'" in compact_sql
    assert "group by tenant_id, run_id, playbook_name" in compact_sql
    assert "group by tenant_id, playbook_name" in compact_sql
    assert "idx_runlog_playbook_success_rate_refresh_unique" in migration_sql
    assert (
        "ON public.runlog_playbook_success_rate (tenant_id, playbook_name)"
        in migration_sql
    )
    assert (
        "DROP MATERIALIZED VIEW IF EXISTS public.runlog_playbook_success_rate"
        in downgrade_sql
    )
