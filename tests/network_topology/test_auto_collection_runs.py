from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.network_topology.collection import runs
from services.network_topology.collection.errors import CollectionError
from tests.network_topology.auto_collection_fakes import FakeRunRepository, plan


@pytest.mark.asyncio
async def test_start_returns_active_when_run_in_progress():
    repo = FakeRunRepository(plans=[plan("p1", enabled=True)])
    first = await runs.start_scheduled("p1", repository=repo, launch=False)
    with pytest.raises(CollectionError) as exc:
        await runs.start_scheduled("p1", repository=repo, launch=False)
    assert exc.value.code == "COLLECTION_RUN_ACTIVE"
    assert repo.runs[first["collection_run_id"]].state == "PENDING"


@pytest.mark.asyncio
async def test_start_rejects_disabled_plan():
    repo = FakeRunRepository(plans=[plan("p1", enabled=False)])
    with pytest.raises(CollectionError) as exc:
        await runs.start_scheduled("p1", repository=repo, launch=False)
    assert exc.value.code == "COLLECTION_PLAN_DISABLED"


@pytest.mark.asyncio
async def test_closed_run_rejects_further_calls():
    repo = FakeRunRepository(plans=[plan("p1", enabled=True)])
    created = await runs.start_scheduled("p1", repository=repo, launch=False)
    run = repo.runs[created["collection_run_id"]]
    await runs.finish_run(run, "FAILED", error_code="X", repository=repo)
    with pytest.raises(CollectionError) as exc:
        await runs.require_open_run("p1", run.id, repository=repo)
    assert exc.value.code == "COLLECTION_RUN_CLOSED"


@pytest.mark.asyncio
async def test_run_of_other_plan_rejected():
    repo = FakeRunRepository(plans=[plan("p1", enabled=True), plan("p2", enabled=True)])
    created = await runs.start_scheduled("p1", repository=repo, launch=False)
    with pytest.raises(CollectionError):
        await runs.require_open_run("p2", created["collection_run_id"], repository=repo)


@pytest.mark.asyncio
async def test_three_failures_trigger_notify(monkeypatch):
    calls = []
    monkeypatch.setattr(runs, "notify_failure", lambda p, r: calls.append(r.id) or _done())
    repo = FakeRunRepository(plans=[plan("p1", enabled=True, notify_enabled=True)])
    for _ in range(3):
        created = await runs.start_scheduled("p1", repository=repo, launch=False)
        await runs.finish_run(repo.runs[created["collection_run_id"]], "FAILED", error_code="X", repository=repo)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_fourth_failure_still_triggers_notify(monkeypatch):
    calls = []
    monkeypatch.setattr(runs, "notify_failure", lambda p, r: calls.append(r.id) or _done())
    repo = FakeRunRepository(plans=[plan("p1", enabled=True, notify_enabled=True)])
    for _ in range(4):
        created = await runs.start_scheduled("p1", repository=repo, launch=False)
        await runs.finish_run(repo.runs[created["collection_run_id"]], "FAILED", error_code="X", repository=repo)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_finish_is_idempotent_and_rejects_nonterminal():
    repo = FakeRunRepository(plans=[plan("p1", enabled=True, notify_enabled=True)])
    created = await runs.start_scheduled("p1", repository=repo, launch=False)
    run = repo.runs[created["collection_run_id"]]
    with pytest.raises(CollectionError) as exc:
        await runs.finish_run(run, "COLLECTING", repository=repo)
    assert exc.value.code == "COLLECTION_RUN_STATE_INVALID"
    await runs.finish_run(run, "FAILED", error_code="X", repository=repo)
    await runs.finish_run(run, "SUCCEEDED", repository=repo)
    assert run.state == "FAILED"
    assert repo.plans["p1"].consecutive_failures == 1


@pytest.mark.asyncio
async def test_manual_background_start_failure_marks_run_failed(monkeypatch):
    from services.network_topology.collection import pipeline

    repo = FakeRunRepository(plans=[plan("p1", enabled=True)])
    def fail(coro, **kwargs):
        coro.close()
        raise RuntimeError("event loop closing")
    monkeypatch.setattr(pipeline, "start_tracked", fail)
    with pytest.raises(RuntimeError):
        await runs.trigger_manual("t1", "topo-1", account_id="acct", repository=repo)
    assert len(repo.runs) == 1
    assert next(iter(repo.runs.values())).state == "FAILED"
    assert next(iter(repo.runs.values())).error_code == "COLLECTION_PIPELINE_START_FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize(("expected", "current", "result"), [(3, 3, "unchanged"), (3, 4, "changed")])
async def test_analysis_revision_classifies_result(expected, current, result):
    repo = FakeRunRepository(plans=[plan("p1", enabled=True)])
    created = await runs.start_scheduled("p1", repository=repo, launch=False)
    run = repo.runs[created["collection_run_id"]]
    run.state = "SUBMITTED"
    run.analysis_run_id = "analysis-1"
    repo.topologies["topo-1"] = SimpleNamespace(
        id="topo-1", tenant_id="t1", current_graph_revision=current, deleted=False
    )
    repo.analyses["analysis-1"] = SimpleNamespace(
        id="analysis-1", tenant_id="t1", topology_id="topo-1", state="SUCCEEDED",
        expected_current_graph_revision=expected, graph_hash="same-after-publish",
    )
    await runs.refresh_from_analysis(run, repository=repo)
    assert run.result == result
    assert run.state == "SUCCEEDED"


async def _done():
    return None


@pytest.mark.asyncio
async def test_notification_failure_preserves_committed_terminal_accounting(monkeypatch, caplog):
    collection_plan = plan("p1", notify_enabled=True, consecutive_failures=2)
    run = SimpleNamespace(id="run-1", state="PENDING")

    async def commit(actual_run, state, finished_at, **kwargs):
        if actual_run.state == "FAILED":
            return None
        actual_run.state = state
        actual_run.finished_at = finished_at
        actual_run.error_code = kwargs["error_code"]
        collection_plan.last_run_id = actual_run.id
        collection_plan.consecutive_failures += 1
        return actual_run, collection_plan

    repo = SimpleNamespace(finish_terminal=commit)
    notification = AsyncMock(side_effect=RuntimeError("INTERNAL_NOTIFICATION_SECRET"))
    monkeypatch.setattr(runs, "notify_failure", notification)
    await runs.finish_run(run, "FAILED", error_code="RESOURCE_NOT_FOUND", repository=repo)
    await runs.finish_run(run, "FAILED", error_code="RESOURCE_NOT_FOUND", repository=repo)
    assert run.state == "FAILED"
    assert run.error_code == "RESOURCE_NOT_FOUND"
    assert collection_plan.consecutive_failures == 3
    assert collection_plan.last_run_id == run.id
    notification.assert_awaited_once()
    assert "notification failed" in caplog.text
    assert "INTERNAL_NOTIFICATION_SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_terminal_persistence_failure_still_propagates(monkeypatch):
    notification = AsyncMock()
    monkeypatch.setattr(runs, "notify_failure", notification)
    repo = SimpleNamespace(finish_terminal=AsyncMock(side_effect=RuntimeError("commit failed")))
    with pytest.raises(RuntimeError, match="commit failed"):
        await runs.finish_run(SimpleNamespace(state="PENDING"), "FAILED", repository=repo)
    notification.assert_not_awaited()
