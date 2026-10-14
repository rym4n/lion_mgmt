import pytest

from services.network_topology.collection import runs
from services.network_topology.collection.errors import CollectionError
from tests.network_topology.auto_collection_fakes import FakeRunRepository, plan


@pytest.mark.asyncio
async def test_start_returns_active_when_run_in_progress():
    repo = FakeRunRepository(plans=[plan("p1", enabled=True)])
    first = await runs.start_scheduled("p1", repository=repo)
    with pytest.raises(CollectionError) as exc:
        await runs.start_scheduled("p1", repository=repo)
    assert exc.value.code == "COLLECTION_RUN_ACTIVE"
    assert repo.runs[first["collection_run_id"]].state == "PENDING"


@pytest.mark.asyncio
async def test_start_rejects_disabled_plan():
    repo = FakeRunRepository(plans=[plan("p1", enabled=False)])
    with pytest.raises(CollectionError) as exc:
        await runs.start_scheduled("p1", repository=repo)
    assert exc.value.code == "COLLECTION_PLAN_DISABLED"


@pytest.mark.asyncio
async def test_closed_run_rejects_further_calls():
    repo = FakeRunRepository(plans=[plan("p1", enabled=True)])
    created = await runs.start_scheduled("p1", repository=repo)
    run = repo.runs[created["collection_run_id"]]
    await runs.finish_run(run, "FAILED", error_code="X", repository=repo)
    with pytest.raises(CollectionError) as exc:
        await runs.require_open_run("p1", run.id, repository=repo)
    assert exc.value.code == "COLLECTION_RUN_CLOSED"


@pytest.mark.asyncio
async def test_run_of_other_plan_rejected():
    repo = FakeRunRepository(plans=[plan("p1", enabled=True), plan("p2", enabled=True)])
    created = await runs.start_scheduled("p1", repository=repo)
    with pytest.raises(CollectionError):
        await runs.require_open_run("p2", created["collection_run_id"], repository=repo)


@pytest.mark.asyncio
async def test_three_failures_trigger_notify(monkeypatch):
    calls = []
    monkeypatch.setattr(runs, "notify_failure", lambda p, r: calls.append(r.id) or _done())
    repo = FakeRunRepository(plans=[plan("p1", enabled=True, notify_enabled=True)])
    for _ in range(3):
        created = await runs.start_scheduled("p1", repository=repo)
        await runs.finish_run(repo.runs[created["collection_run_id"]], "FAILED", error_code="X", repository=repo)
    assert len(calls) == 1


async def _done():
    return None
