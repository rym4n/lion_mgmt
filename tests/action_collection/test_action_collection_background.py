import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.action_collection import background, notify


@pytest.fixture
def run_model():
    class RunModel:
        beats = []
        fail = False

        @classmethod
        def filter(cls, **kw):
            assert kw == {"id": "run-1", "tenant_id": "t1", "deleted": False}

            async def update(**values):
                if cls.fail:
                    raise RuntimeError("SECRET database failure")
                cls.beats.append(values["heartbeat_at"])
                return 1

            return SimpleNamespace(update=update)

    return RunModel


def start(coro, run_model):
    return background.start_tracked(coro, run=SimpleNamespace(id="run-1", tenant_id="t1"),
                                    run_model=run_model, interval=0.01)


@pytest.mark.asyncio
async def test_immediate_completion_still_writes_initial_heartbeat(run_model):
    async def work():
        assert len(run_model.beats) == 1
        return "done"

    task = start(work(), run_model)
    assert task in background._ACTIVE_TASKS
    assert await task == "done"
    await asyncio.sleep(0)
    assert task not in background._ACTIVE_TASKS
    assert run_model.beats[0].utcoffset() == timedelta(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["complete", "error", "cancel"])
async def test_heartbeat_stops_and_tracking_cleans_up(run_model, caplog, outcome):
    release = asyncio.Event()

    async def work():
        await release.wait()
        if outcome == "error":
            raise RuntimeError("SECRET executor output")
        return "done"

    task = start(work(), run_model)
    try:
        await asyncio.sleep(0.045)
        assert len(run_model.beats) >= 2
        assert all(beat.tzinfo is not None for beat in run_model.beats)
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            if outcome == "error":
                with pytest.raises(RuntimeError):
                    await task
            else:
                assert await task == "done"
        count = len(run_model.beats)
        await asyncio.sleep(0.03)
        assert len(run_model.beats) == count
        assert task not in background._ACTIVE_TASKS
        assert "SECRET" not in caplog.text
        if outcome == "error":
            assert any(record.levelno == logging.ERROR for record in caplog.records)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_before_start_closes_owned_coroutine(run_model):
    async def work():
        pytest.fail("cancelled work must not run")

    coro = work()
    task = start(coro, run_model)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED
    assert task not in background._ACTIVE_TASKS


@pytest.mark.asyncio
async def test_heartbeat_failure_does_not_stop_work_or_log_error_text(run_model, caplog):
    run_model.fail = True

    async def work():
        await asyncio.sleep(0.025)
        return 1

    assert await start(work(), run_model) == 1
    assert "heartbeat failed" in caplog.text
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize("naive_now", [False, True])
def test_heartbeat_stale_uses_heartbeat_then_started_at(naive_now):
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(seconds=121)
    fresh = now - timedelta(seconds=10)
    supplied_now = now.replace(tzinfo=None) if naive_now else now
    assert background.heartbeat_stale(SimpleNamespace(heartbeat_at=old, started_at=fresh), supplied_now)
    assert not background.heartbeat_stale(SimpleNamespace(heartbeat_at=fresh, started_at=old), supplied_now)
    assert background.heartbeat_stale(SimpleNamespace(heartbeat_at=None, started_at=old.replace(tzinfo=None)), supplied_now)
    assert not background.heartbeat_stale(SimpleNamespace(started_at=None), supplied_now)
    assert not background.heartbeat_stale(SimpleNamespace(started_at=now - timedelta(seconds=120)), supplied_now)


@pytest.mark.asyncio
async def test_notify_after_commit_threshold_and_swallow(caplog):
    run = SimpleNamespace(id="run-1", state="FAILED")
    sender = AsyncMock()
    for plan in [None, SimpleNamespace(notify_enabled=True, consecutive_failures=2),
                 SimpleNamespace(notify_enabled=False, consecutive_failures=5)]:
        await notify.notify_after_commit(plan, run, notify=sender)
    sender.assert_not_awaited()
    eligible_plan = SimpleNamespace(notify_enabled=True, consecutive_failures=3)
    await notify.notify_after_commit(eligible_plan, run, notify=sender)
    sender.assert_awaited_once_with(eligible_plan, run)
    failing = AsyncMock(side_effect=RuntimeError("SECRET"))
    await notify.notify_after_commit(SimpleNamespace(notify_enabled=True, consecutive_failures=5), run, notify=failing)
    assert "notification failed" in caplog.text and "SECRET" not in caplog.text
    await notify.notify_after_commit(SimpleNamespace(notify_enabled=True, consecutive_failures=5),
                                     SimpleNamespace(id="r", state="PARTIAL"), notify=sender)
    assert sender.await_count == 1


@pytest.mark.asyncio
async def test_notify_failure_publishes_each_configured_action_for_plan_tenant(monkeypatch):
    published = []

    async def publish(topic, message):
        published.append((topic, message))

    monkeypatch.setattr(notify, "aproduce", publish)
    action = {"func": "send", "name": "Send alert", "tool_name": "mail", "version": "v1",
              "tool_main": "main.py", "resource": "resource-1", "description": "Alert",
              "action_input": {"body": "Result: ${采集结果}"}, "retry_times": "2", "timeout": "bad"}
    plan = SimpleNamespace(tenant_id="tenant-plan", notify_config={"actions": [action, action]})
    run = SimpleNamespace(error_message="failed collection", triggered_by={"id": "account-1"})
    await notify.notify_failure(plan, run)
    assert len(published) == 2
    assert published[0][1]["run_id"] != published[1][1]["run_id"]
    for topic, message in published:
        assert topic == notify.TASK_TOPIC
        assert message["run_type"] == "action"
        assert message["tenant_id"] == "tenant-plan"
        assert message["run_args"] == {
            "resource": "resource-1", "node_name": "Alert", "action": "send",
            "action_name": "Send alert", "app_name": "mail", "name": "Alert",
            "app_ver": "v1", "main": "main.py", "action_input": {"body": "Result: failed collection"},
            "retry_times": 2, "timeout": 300, "executor": "account-1",
        }
