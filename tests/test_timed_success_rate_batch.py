from types import SimpleNamespace

import pytest

from libs.chat import ChatManager
from libs.timed_run_summary import refresh_timed_run_summaries
from services.timed.views import timers_success_rate


def _transaction(rows, captured):
    class FakeConnection:
        async def execute_query_dict(self, sql, params):
            captured["calls"] = captured.get("calls", 0) + 1
            captured["sql"] = sql
            captured["params"] = params
            return rows

    class FakeTransaction:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, *_args):
            return False

    return FakeTransaction()


@pytest.mark.asyncio
async def test_timers_success_rate_batches_tasks_and_keeps_empty_results(monkeypatch):
    saved = []

    def timed(timer_id, success_rate, result):
        item = SimpleNamespace(id=timer_id, success_rate=success_rate, result=result)

        async def save(**kwargs):
            saved.append((timer_id, kwargs))

        item.save = save
        return item

    items = [
        timed("timer-1", "0.0%", "fail"),
        timed("timer-2", None, None),
    ]
    rows = [
        {
            "timer_id": "timer-1",
            "run_count": 2,
            "success_count": 2,
            "latest_is_running": False,
            "latest_is_success": True,
        }
    ]
    captured = {}
    monkeypatch.setattr(
        "services.timed.views.in_transaction",
        lambda: _transaction(rows, captured),
    )

    result = await timers_success_rate(items)

    assert captured["calls"] == 1
    assert captured["params"] == [["timer-1", "timer-2"]]
    assert "FROM timed_run_summary" in captured["sql"]
    assert "PARTITION BY timer_id" in captured["sql"]
    assert "WHERE run_rank <= 10" in captured["sql"]
    assert saved == [
        ("timer-1", {"update_fields": ["success_rate", "result", "utime"]})
    ]
    assert result == [
        {"id": "timer-1", "result": "success", "success_rate": "100.0%"},
        {"id": "timer-2", "result": None, "success_rate": None},
    ]


@pytest.mark.asyncio
async def test_timed_run_summary_migration_backfills_and_prunes():
    migration = __import__(
        "migrations.models.111_20260819163000_materialize_timed_run_summary",
        fromlist=["upgrade"],
    )

    sql = (await migration.upgrade(None)).lower()
    assert "create table if not exists public.timed_run_summary" in sql
    assert "from public.runlog" in sql
    assert "where timer_id is not null and deleted = false" in sql
    assert "where run_rank <= 10" in sql
    assert "create index if not exists idx_timed_run_summary_recent" in sql


@pytest.mark.asyncio
async def test_refresh_timed_run_summaries_rebuilds_and_prunes_affected_runs():
    calls = []

    class FakeConnection:
        async def execute_query_dict(self, sql, params):
            calls.append(("query_dict", sql, params))
            return [
                {"timer_id": "timer-1", "run_id": "run-1"},
                {"timer_id": "timer-1", "run_id": "run-2"},
            ]

        async def execute_query(self, sql, params):
            calls.append(("query", sql, params))

    runlogs = [
        SimpleNamespace(timer_id="timer-1", run_id="run-2"),
        SimpleNamespace(timer_id="timer-1", run_id="run-1"),
        SimpleNamespace(timer_id="timer-1", run_id="run-2"),
    ]

    await refresh_timed_run_summaries(FakeConnection(), runlogs)

    assert len(calls) == 5
    assert calls[0][2] == [["run-1", "run-2"]]
    assert "FROM timed" in calls[1][1]
    assert "FOR NO KEY UPDATE" in calls[1][1]
    assert calls[1][2] == [["timer-1"]]
    assert "DELETE FROM timed_run_summary" in calls[2][1]
    assert "INSERT INTO timed_run_summary" in calls[3][1]
    assert calls[2][2] == [["timer-1", "timer-1"], ["run-1", "run-2"]]
    assert calls[3][2] == [
        ["timer-1", "timer-1"],
        ["run-1", "run-2"],
        [1, 2],
        [0],
    ]
    assert "ANY($3::integer[])" in calls[3][1]
    assert "ANY($4::integer[])" in calls[3][1]
    assert "WHERE run_rank > 10" in calls[4][1]
    assert calls[4][2] == [["timer-1"]]


@pytest.mark.asyncio
async def test_runlog_upsert_refreshes_summary_in_the_same_transaction(monkeypatch):
    connection = SimpleNamespace(execute_query=None)
    inserted = []
    refreshed = []

    async def execute_query(sql, params):
        inserted.append((sql, params))

    connection.execute_query = execute_query

    class FakeTransaction:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *_args):
            return False

    async def refresh(conn, runlogs):
        refreshed.append((conn, runlogs))

    monkeypatch.setattr("libs.chat.in_transaction", lambda: FakeTransaction())
    monkeypatch.setattr("libs.chat.refresh_timed_run_summaries", refresh)
    runlog = SimpleNamespace(
        id="log-1",
        tenant_id="tenant-1",
        run_id="run-1",
        event_id=None,
        node_id="node-1",
        node_name="node",
        node_type="action-node",
        tool_name="tool",
        tool_version="1.0",
        tool_display_name="Tool",
        action_name="action",
        action_display_name="Action",
        status=0,
        args=None,
        result="ok",
        start_time=None,
        end_time=None,
        executor=None,
        resource_name="resource",
        playbook_name="flow",
        playbook_version_id="version-1",
        timer_id="timer-1",
    )

    await ChatManager.__new__(ChatManager)._bulk_upsert_runlogs([runlog])

    assert len(inserted) == 1
    assert 'INSERT INTO "runlog"' in inserted[0][0]
    assert refreshed == [(connection, [runlog])]
