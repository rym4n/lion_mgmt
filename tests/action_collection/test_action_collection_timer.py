from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.action_collection import timer

NOW = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)


@asynccontextmanager
async def same_plan(plan):
    yield plan


class TimerRows:
    row = None

    @classmethod
    def filter(cls, **kwargs):
        return SimpleNamespace(first=AsyncMock(side_effect=lambda: cls.row))


def make_plan(**updates):
    values = dict(id="plan", tenant_id="tenant", enabled=True,
                  schedule_cron="0 0 3 * * *", scheduled_cron=None,
                  next_run_at=None, timed_id=None, save=AsyncMock())
    return SimpleNamespace(**(values | updates))


@pytest.fixture(autouse=True)
def reset_rows():
    TimerRows.row = None


@pytest.mark.asyncio
async def test_enabled_plan_initializes_direct_schedule_without_playbooks_or_kafka():
    plan = make_plan()
    produce = AsyncMock(side_effect=AssertionError("fresh collection uses no flow task"))
    await timer.reconcile_timer_locked(plan, timer_prefix="collection_", locked_plan=same_plan,
                                       timed_model=TimerRows, produce=produce, now=NOW)
    assert plan.scheduled_cron == "0 0 3 * * *"
    assert plan.next_run_at == datetime(2026, 10, 22, 19, 0, tzinfo=timezone.utc)
    assert plan.timed_id is None


@pytest.mark.asyncio
async def test_unchanged_schedule_retains_due_cursor_and_disable_clears_it():
    due = datetime(2026, 10, 21, 19, 0, tzinfo=timezone.utc)
    plan = make_plan(scheduled_cron="0 0 3 * * *", next_run_at=due)
    await timer.reconcile_timer_locked(plan, timer_prefix="x_", locked_plan=same_plan,
                                       timed_model=TimerRows, now=NOW)
    assert plan.next_run_at == due
    plan.enabled = False
    await timer.reconcile_timer_locked(plan, timer_prefix="x_", locked_plan=same_plan,
                                       timed_model=TimerRows, now=NOW)
    assert plan.next_run_at is None and plan.scheduled_cron is None


@pytest.mark.asyncio
async def test_legacy_job_removal_is_retryable_and_never_recreates_playbook_timer():
    plan = make_plan(timed_id="legacy")
    row = SimpleNamespace(id="legacy", enable=1, deleted=False,
                          playbook_input={"flow_id": "old"}, save=AsyncMock())
    TimerRows.row = row
    produce = AsyncMock(side_effect=[RuntimeError("offline"), None])
    with pytest.raises(RuntimeError, match="offline"):
        await timer.reconcile_timer_locked(plan, timer_prefix="x_", locked_plan=same_plan,
                                           timed_model=TimerRows, produce=produce, now=NOW)
    assert row.enable == 0 and not row.deleted and plan.timed_id == "legacy"
    await timer.reconcile_timer_locked(plan, timer_prefix="x_", locked_plan=same_plan,
                                       timed_model=TimerRows, produce=produce, now=NOW)
    assert row.deleted and plan.timed_id is None
    assert [call.args[1]["timer"] for call in produce.await_args_list] == ["remove", "remove"]
    assert {call.args[1]["timer_id"] for call in produce.await_args_list} == {"legacy"}


@pytest.mark.asyncio
async def test_stale_reconciliation_uses_latest_persisted_schedule():
    stale = make_plan(schedule_cron="0 0 2 * * *")
    current = make_plan(schedule_cron="0 0 4 * * *")

    @asynccontextmanager
    async def latest(_):
        yield current

    await timer.reconcile_timer_locked(stale, timer_prefix="x_", locked_plan=latest,
                                       timed_model=TimerRows, now=NOW)
    assert current.next_run_at == datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_advisory_lock_filters_pool_options_and_closes_connection(monkeypatch):
    client = SimpleNamespace(host="db.example", port=5432, user="test", password="test",
                             database="db", server_settings={},
                             extra={"command_timeout": 12, "min_size": 1, "max_size": 4,
                                    "max_queries": 100, "max_inactive_connection_lifetime": 60,
                                    "connect": object(), "setup": object(), "init": object(), "reset": object()})
    connection = AsyncMock()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(timer.connections, "get", lambda _: client)
    monkeypatch.setattr(timer.asyncpg, "connect", connect)
    async with timer.advisory_lock("namespace", "tenant", "plan"):
        connection.execute.assert_awaited_once()
    assert connect.await_args.kwargs["command_timeout"] == 12
    assert not set(timer._POOL_ONLY_OPTIONS) & connect.await_args.kwargs.keys()
    connection.close.assert_awaited_once()
