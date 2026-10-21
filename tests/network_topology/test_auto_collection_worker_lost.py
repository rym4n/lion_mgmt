"""Topology workers persist liveness and preserve lifecycle timeout priority."""
from datetime import datetime, timedelta, timezone

import pytest

from models.network_topology_v2 import TopologyCollectionRun
from services.action_collection.background import start_tracked
from services.network_topology.collection import dispatch, runs
from tests.network_topology.auto_collection_fakes import FakeRunRepository, plan, run_row


def test_run_model_has_nullable_heartbeat():
    assert TopologyCollectionRun._meta.fields_map["heartbeat_at"].null is True


@pytest.mark.asyncio
@pytest.mark.parametrize("heartbeat", ["stale", "absent", "naive"])
async def test_sweep_marks_stale_heartbeat_worker_lost(heartbeat):
    repo = FakeRunRepository(plans=[plan("p1"), plan("p2", topology_id="topo-2")])
    now = datetime.now(timezone.utc)
    stale = repo.runs[(await runs.start_scheduled("p1", repository=repo, launch=False))["collection_run_id"]]
    stale.state = "COLLECTING"
    stale.started_at = now - timedelta(minutes=10)
    stale.heartbeat_at = now - timedelta(minutes=5)
    if heartbeat == "absent":
        stale.heartbeat_at = None
    elif heartbeat == "naive":
        stale.heartbeat_at = stale.heartbeat_at.replace(tzinfo=None)
    alive = repo.runs[(await runs.start_scheduled("p2", repository=repo, launch=False))["collection_run_id"]]
    alive.state = "COLLECTING"
    alive.started_at = now - timedelta(minutes=10)
    alive.heartbeat_at = now - timedelta(seconds=10)

    assert await runs.sweep_runs(repository=repo) == 1
    assert (stale.state, stale.error_code) == ("FAILED", "COLLECTION_WORKER_LOST")
    assert alive.state == "COLLECTING"
    assert repo.plans["p1"].consecutive_failures == 1
    assert await runs.sweep_runs(repository=repo) == 0
    assert repo.plans["p1"].consecutive_failures == 1


@pytest.mark.asyncio
async def test_sweep_prefers_run_timeout_over_worker_lost():
    repo = FakeRunRepository(plans=[plan("p1", run_timeout_seconds=600)])
    run = repo.runs[(await runs.start_scheduled("p1", repository=repo, launch=False))["collection_run_id"]]
    run.state = "COLLECTING"
    run.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    run.heartbeat_at = None
    assert await runs.sweep_runs(repository=repo) == 1
    assert run.error_code == "COLLECTION_RUN_TIMEOUT"


@pytest.mark.asyncio
async def test_sweep_ignores_pending_runs_without_heartbeat():
    repo = FakeRunRepository(plans=[plan("p1")])
    run = repo.runs[(await runs.start_scheduled("p1", repository=repo, launch=False))["collection_run_id"]]
    run.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert await runs.sweep_runs(repository=repo) == 0
    assert run.state == "PENDING"


@pytest.mark.asyncio
async def test_background_claim_initializes_heartbeat_and_uses_tracked_worker(monkeypatch):
    row = run_row("run")
    row.started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    updates, filters, tasks, collected = [], [], [], []

    class Query:
        async def update(self, **values):
            updates.append(values)
            return 1

    class RunModel:
        @staticmethod
        def filter(**values):
            filters.append(values)
            return Query()

    def tracked(coro, *, run, run_model):
        assert run is row
        assert run_model is RunModel
        task = start_tracked(coro, run=run, run_model=run_model)
        tasks.append(task)
        return task

    async def collect(*args, **kwargs):
        collected.append(row.id)

    monkeypatch.setattr(dispatch, "TopologyCollectionRun", RunModel)
    monkeypatch.setattr(dispatch, "start_tracked", tracked, raising=False)
    monkeypatch.setattr(dispatch, "collect_and_store", collect)
    result = await dispatch.start_background(plan("p"), row)
    assert tasks, "background collection must use shared heartbeat tracking"
    await tasks[0]
    assert result == {"collection_run_id": "run", "run_state": "COLLECTING"}
    assert collected == ["run"]
    assert updates[0]["state"] == "COLLECTING"
    assert updates[0]["heartbeat_at"].tzinfo is not None
    assert updates[1]["heartbeat_at"] >= updates[0]["heartbeat_at"]
    assert filters == [
        dict(id="run", tenant_id="t1", topology_id="topo-1", deleted=False, state="MANIFEST_FROZEN"),
        dict(id="run", tenant_id="t1", deleted=False),
    ]
