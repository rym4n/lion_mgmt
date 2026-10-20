"""Sweep decisions and listener lifecycle without external services."""
import asyncio
from copy import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.network_access_control.collection import pipeline, runs
from tests.network_access_control.policy_collection_fakes import FakePolicyRunRepository, plan, run_row
from tests.network_access_control.test_policy_collection_runs import orm  # shared in-memory ORM fixture

NOW = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(runs, "notify_after_commit", AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["COLLECTING", "NORMALIZING", "APPLYING"])
async def test_sweep_marks_stale_worker_once_and_accounts_once(state):
    p = plan(consecutive_failures=2)
    repo = FakePolicyRunRepository(plans=[p])
    row = run_row(state=state, started_at=NOW - timedelta(minutes=5),
                  heartbeat_at=NOW - timedelta(seconds=121))
    repo.runs[row.id] = row
    assert await runs.sweep_runs(now=NOW, repository=repo) == 1
    assert (row.state, row.error_code) == ("FAILED", "COLLECTION_WORKER_LOST")
    assert p.consecutive_failures == 3 and p.last_run_id == row.id
    assert await runs.sweep_runs(now=NOW, repository=repo) == 0
    assert p.consecutive_failures == 3
    runs.notify_after_commit.assert_awaited_once_with(p, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PENDING", "COLLECTING", "NORMALIZING", "APPLYING"])
async def test_timeout_precedes_lost_worker_in_every_open_state(state):
    repo = FakePolicyRunRepository(plans=[plan(run_timeout_seconds=600)])
    row = run_row(state=state, started_at=NOW - timedelta(seconds=601),
                  heartbeat_at=NOW - timedelta(seconds=121))
    repo.runs[row.id] = row
    assert await runs.sweep_runs(now=NOW, repository=repo) == 1
    assert row.error_code == "COLLECTION_RUN_TIMEOUT"


@pytest.mark.asyncio
async def test_sweep_keeps_pending_fresh_and_exact_thresholds_and_accepts_naive_utc():
    repo = FakePolicyRunRepository(plans=[plan(run_timeout_seconds=600)])
    for row in [run_row("pending", started_at=NOW - timedelta(seconds=599)),
                run_row("fresh", state="COLLECTING", started_at=NOW - timedelta(seconds=600),
                        heartbeat_at=NOW - timedelta(seconds=30)),
                run_row("threshold", state="NORMALIZING", started_at=NOW - timedelta(seconds=600),
                        heartbeat_at=(NOW - timedelta(seconds=120)).replace(tzinfo=None))]:
        repo.runs[row.id] = row
    assert await runs.sweep_runs(now=NOW.replace(tzinfo=None), repository=repo) == 0
    runs.notify_after_commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_uses_same_tenant_plan_and_default_timeout_for_missing_plan():
    foreign = plan(tenant_id="t2", run_timeout_seconds=600, consecutive_failures=4)
    repo = FakePolicyRunRepository(plans=[foreign])
    row = run_row(started_at=NOW - timedelta(seconds=601))
    repo.runs[row.id] = row
    assert await runs.sweep_runs(now=NOW, repository=repo) == 0
    row.started_at = NOW - timedelta(seconds=3601)
    assert await runs.sweep_runs(now=NOW, repository=repo) == 1
    assert row.error_code == "COLLECTION_RUN_TIMEOUT"
    assert foreign.consecutive_failures == 4 and foreign.last_run_id is None
    runs.notify_after_commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_isolates_bad_rows_and_cleanup_failure(monkeypatch, caplog):
    repo = FakePolicyRunRepository(plans=[plan(), plan("bad-plan", run_timeout_seconds="bad")])
    repo.runs["bad"] = run_row("bad", plan_id="bad-plan")
    repo.runs["good"] = run_row("good", state="NORMALIZING", started_at=NOW - timedelta(minutes=5))
    monkeypatch.setattr(repo, "clear_expired_raw_outputs", AsyncMock(side_effect=ValueError("RAW SECRET")))
    assert await runs.sweep_runs(now=NOW, repository=repo) == 1
    assert repo.runs["good"].error_code == "COLLECTION_WORKER_LOST"
    assert repo.runs["bad"].state == "PENDING"
    assert "RAW SECRET" not in caplog.text
    assert "cleanup" in caplog.text and "bad" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["orm", "fake"])
async def test_sweep_retention_clears_only_old_live_terminal_rows(orm, backend):
    repo, rows, _ = orm
    cutoff = NOW - timedelta(days=7)
    samples = [run_row("old", state="SUCCEEDED", finished_at=cutoff - timedelta(microseconds=1)),
               run_row("foreign-old", tenant_id="t2", state="FAILED", finished_at=cutoff - timedelta(days=1)),
               run_row("boundary", state="PARTIAL", finished_at=cutoff),
               run_row("new", tenant_id="t2", state="CANCELLED", finished_at=cutoff + timedelta(seconds=1)),
               run_row("active", tenant_id="t2", finished_at=cutoff - timedelta(days=1), started_at=NOW),
               run_row("deleted", state="FAILED", deleted=True, finished_at=cutoff - timedelta(days=1)),
               run_row("unfinished", state="FAILED", finished_at=None)]
    for row in samples:
        row.raw_outputs = "keep-or-expire"
    if backend == "fake":
        repo = FakePolicyRunRepository()
        repo.runs = {row.id: row for row in samples}
    else:
        rows["NetworkAccessPolicyCollectionRun"].extend(samples)
    assert runs.RAW_OUTPUT_RETENTION == timedelta(days=7)
    assert await runs.sweep_runs(now=NOW, repository=repo) == 0
    assert {row.id for row in samples if row.raw_outputs is None} == {"old", "foreign-old"}


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["heartbeat", "stage", "terminal", "deleted"])
async def test_sweep_rechecks_live_row_under_lock_before_counting_or_notifying(orm, monkeypatch, race):
    repo, rows, calls = orm
    p = plan(consecutive_failures=1, save=AsyncMock())
    row = run_row(state="COLLECTING", started_at=NOW - timedelta(minutes=5), save=AsyncMock())
    stale = copy(row)
    rows["NetworkAccessPolicyCollectionRun"].append(row)
    rows["NetworkAccessPolicyCollectionPlan"].append(p)
    monkeypatch.setattr(repo, "open_runs", AsyncMock(return_value=[stale]))
    finish = repo.finish_terminal

    async def racing_finish(*args, **kwargs):
        if race == "heartbeat":
            row.heartbeat_at = NOW
        elif race == "stage":
            row.state = "NORMALIZING"
            row.heartbeat_at = NOW
        elif race == "terminal":
            row.state = "CANCELLED"
        else:
            row.deleted = True
        return await finish(*args, **kwargs)

    monkeypatch.setattr(repo, "finish_terminal", racing_finish)
    assert await runs.sweep_runs(now=NOW, repository=repo) == 0
    assert row.error_code is None and p.consecutive_failures == 1 and p.last_run_id is None
    runs.notify_after_commit.assert_not_awaited()
    row.save.assert_not_awaited()
    assert any(kind == "lock" for kind, _ in calls)


@pytest.mark.asyncio
async def test_terminal_guard_is_faithful_in_fake_repository():
    repo = FakePolicyRunRepository(plans=[plan()])
    row = run_row(state="COLLECTING")
    repo.runs[row.id] = row
    stale = copy(row)
    assert await runs.finish_run(stale, "FAILED", expected_state="COLLECTING",
                                 terminal_guard=lambda current: current.heartbeat_at is not None,
                                 repository=repo) is False
    assert row.state == "COLLECTING" and repo.plans["p1"].last_run_id is None
    runs.notify_after_commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["normalize", "apply", "compare"])
async def test_sweep_stops_later_stage_side_effects_with_detached_worker(monkeypatch, stage):
    repo = FakePolicyRunRepository(plans=[plan()])
    states = {"normalize": "NORMALIZING", "apply": "APPLYING", "compare": "APPLYING"}
    row = run_row(state=states[stage], started_at=NOW - timedelta(minutes=5))
    repo.runs[row.id] = row
    stale = copy(row)
    normalizer, apply, compare = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    monkeypatch.setattr(pipeline.manager, "run_policy_compare", compare)
    assert await runs.sweep_runs(now=NOW, repository=repo) == 1
    if stage == "normalize":
        assert await pipeline.normalize_stage(plan(), stale, [{"resource_id": "r1"}],
                                               normalizer=normalizer, repository=repo) == []
    elif stage == "apply":
        assert await pipeline.apply_stage(plan(), stale, [{"resource_id": "r1"}], repository=repo) == []
    else:
        await pipeline.compare_stage(plan(), stale, [{"device_asset_id": "a1"}], repository=repo)
    normalizer.assert_not_awaited()
    apply.assert_not_awaited()
    compare.assert_not_awaited()
    assert stale.state == row.state == "FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("sweep_during", ["collect", "normalize", "apply", "compare"])
async def test_pipeline_discards_inflight_result_after_sweep_and_stops_remaining_stages(monkeypatch, sweep_during):
    from dataclasses import asdict
    from tests.network_access_control.test_policy_collection_pipeline_collect import outcome, target

    p = plan()
    repo = FakePolicyRunRepository(plans=[p])
    row = run_row(state="COLLECTING", started_at=NOW - timedelta(minutes=5),
                  device_total=1, frozen_targets=[asdict(target(asset="a1"))])
    repo.runs[row.id] = row
    worker = copy(row)
    visited = []

    async def visit(stage):
        visited.append(stage)
        if stage == sweep_during:
            assert await runs.sweep_runs(now=NOW, repository=repo) == 1

    async def collect(*args, **kwargs):
        await visit("collect")
        return [outcome(output={"policies": []})]

    async def normalize(*args, **kwargs):
        await visit("normalize")
        return {"r1": pipeline.NormalizeResult("r1", "success", {"policies": []}, None)}

    async def apply(*args, **kwargs):
        await visit("apply")
        return {"added": 0}

    async def compare(*args, **kwargs):
        await visit("compare")
        return {"id": "must-not-persist"}

    monkeypatch.setattr(pipeline, "execute_targets", collect)
    monkeypatch.setattr(pipeline, "max_rules_per_device", AsyncMock(return_value=20000))
    monkeypatch.setattr(pipeline.manager, "apply_actual_policies", apply)
    monkeypatch.setattr(pipeline.manager, "run_policy_compare", compare)
    await pipeline.run_pipeline(p, worker, normalizer=normalize, repository=repo)
    stages = ["collect", "normalize", "apply", "compare"]
    assert visited == stages[:stages.index(sweep_during) + 1]
    assert (row.state, row.error_code) == ("FAILED", "COLLECTION_WORKER_LOST")
    assert row.compare_run_id is None and row.compare_error is None
    assert p.consecutive_failures == 1 and p.last_run_id == row.id
    if sweep_during == "collect":
        assert row.raw_outputs is None and row.item_results is None
    elif sweep_during == "normalize":
        assert row.item_results[0]["stage"] == "collect"
    elif sweep_during == "apply":
        assert row.item_results[0]["stage"] == "normalize"
    runs.notify_after_commit.assert_awaited_once_with(p, row)


@pytest.mark.asyncio
async def test_listener_survives_failed_round_and_stops_task(monkeypatch):
    from core.listeners import LISTENER_TUPLE
    from core.listeners.policy_collection import PolicyCollectionListener

    assert LISTENER_TUPLE[-1] is PolicyCollectionListener
    listener = PolicyCollectionListener()
    app = SimpleNamespace(ctx=SimpleNamespace())
    real_sleep = asyncio.sleep
    intervals = []
    block = asyncio.Event()
    rounds_done = asyncio.Event()
    round_count = 0

    async def controlled_sleep(seconds):
        intervals.append(seconds)
        if len(intervals) > 2:
            await block.wait()
        await real_sleep(0)

    async def sweep():
        nonlocal round_count
        round_count += 1
        if round_count == 1:
            raise RuntimeError("round failed")
        rounds_done.set()

    monkeypatch.setattr(asyncio, "sleep", controlled_sleep)
    monkeypatch.setattr(runs, "sweep_runs", sweep)
    try:
        await listener.after_server_start(app, None)
        task = app.ctx.policy_collection_sweep_task
        await listener.after_server_start(app, None)
        assert app.ctx.policy_collection_sweep_task is task
        await asyncio.wait_for(rounds_done.wait(), timeout=2)
        assert round_count == 2 and intervals[:2] == [60, 60] and not task.done()
    finally:
        await listener.before_server_stop(app, None)
    assert task.cancelled()
    await listener.after_server_start(app, None)
    replacement = app.ctx.policy_collection_sweep_task
    assert replacement is not task
    await listener.before_server_stop(app, None)
    assert replacement.cancelled()


@pytest.mark.asyncio
async def test_listener_stop_without_start_is_harmless():
    from core.listeners.policy_collection import PolicyCollectionListener
    await PolicyCollectionListener().before_server_stop(SimpleNamespace(ctx=SimpleNamespace()), None)
