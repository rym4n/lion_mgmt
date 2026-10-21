"""Direct backend collection owns progression without a maintenance flow."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from models.playbook import Playbook, PlaybookVersion
from services.network_topology.collection import runs
from services.network_topology.collection.errors import CollectionError
from tests.network_topology.auto_collection_fakes import FakeRunRepository, plan


@pytest.mark.asyncio
async def test_manual_without_published_playbook_launches_direct_collection(monkeypatch):
    repo = FakeRunRepository(plans=[plan("p1")])
    unavailable = Mock(return_value=SimpleNamespace(first=AsyncMock(return_value=None)))
    launch = AsyncMock()
    publish = AsyncMock(side_effect=AssertionError("collection cannot publish a flow"))
    monkeypatch.setattr(Playbook, "filter", unavailable)
    monkeypatch.setattr(PlaybookVersion, "filter", unavailable)
    monkeypatch.setattr(runs, "launch_run", launch, raising=False)
    monkeypatch.setattr(runs, "aproduce", publish, raising=False)

    result = await runs.trigger_manual("t1", "topo-1", account_id="acct", repository=repo)

    row = repo.runs[result["collection_run_id"]]
    assert row.state == "PENDING" and row.triggered_by == "acct"
    assert getattr(row, "flow_run_id", None) is None
    launch.assert_awaited_once_with("p1", row.id, repository=repo)
    unavailable.assert_not_called()
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_reusing_active_run_does_not_launch_again(monkeypatch):
    repo = FakeRunRepository(plans=[plan("p1")])
    launch = AsyncMock()
    monkeypatch.setattr(runs, "launch_run", launch, raising=False)

    first = await runs.trigger_manual("t1", "topo-1", account_id="acct", repository=repo)
    second = await runs.trigger_manual("t1", "topo-1", account_id="acct", repository=repo)

    assert second == {**first, "reused": True}
    assert len(repo.runs) == 1
    launch.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduled_launch_can_wait_for_claim_transaction_commit(monkeypatch):
    repo = FakeRunRepository(plans=[plan("p1")])
    launch = AsyncMock()
    monkeypatch.setattr(runs, "launch_run", launch, raising=False)
    delayed = await runs.start_scheduled("p1", repository=repo, launch=False)
    launch.assert_not_awaited()
    assert repo.runs[delayed["collection_run_id"]].state == "PENDING"
    repo.runs[delayed["collection_run_id"]].state = "CANCELLED"

    started = await runs.start_scheduled("p1", repository=repo)
    launch.assert_awaited_once_with("p1", started["collection_run_id"], repository=repo)


def _stages(monkeypatch, *, fail_at=None, cancel_at=None, expire_at=None):
    from services.network_topology.collection import pipeline

    stages = []

    async def stage(name, row):
        stages.append(name)
        if fail_at == name:
            raise CollectionError("RESOURCE_NOT_FOUND", 409)
        if cancel_at == name:
            row.state = "CANCELLED"
        if expire_at == name:
            row.started_at -= timedelta(hours=2)

    async def freeze(collection_plan, items):
        assert items == collection_plan.items
        await stage("freeze", repo.runs[run_id])
        return [SimpleNamespace(resource_id="r1")]

    async def issue(collection_plan, row, targets, *, now):
        assert now.utcoffset().total_seconds() == 0
        await stage("manifest", row)
        row.state = "MANIFEST_FROZEN"
        row.manifest_id = "manifest-1"
        row.device_total = 1

    async def collect(collection_plan, row, *, repository):
        assert row.state == "COLLECTING"
        await stage("collect", row)
        if row.state != "CANCELLED":
            row.state = "COLLECTED"
            row.snapshot_draft = '{"snapshot":"draft"}'

    async def submit(collection_plan, row):
        assert row.snapshot_draft
        await stage("submit", row)
        row.state = "SUBMITTED"
        row.analysis_run_id = "analysis-1"
        row.snapshot_id = "snapshot-1"
        row.snapshot_draft = None

    repo = FakeRunRepository(plans=[plan("p1")])
    run_id = None
    monkeypatch.setattr(pipeline, "freeze_targets", freeze)
    monkeypatch.setattr(pipeline, "issue_action_manifest", issue)
    monkeypatch.setattr(pipeline, "collect_and_store", collect)
    monkeypatch.setattr(pipeline, "submit_snapshot", submit)

    async def create():
        nonlocal run_id
        result = await runs.start_scheduled("p1", repository=repo, launch=False)
        run_id = result["collection_run_id"]
        return repo.runs[run_id]

    return pipeline, repo, stages, create


@pytest.mark.asyncio
async def test_direct_pipeline_submits_snapshot_and_waits_for_analysis(monkeypatch):
    pipeline, repo, stages, create = _stages(monkeypatch)
    row = await create()

    await pipeline.run_pipeline("p1", row.id, repository=repo)

    assert stages == ["freeze", "manifest", "collect", "submit"]
    assert (row.state, row.analysis_run_id, row.snapshot_id) == ("SUBMITTED", "analysis-1", "snapshot-1")
    assert repo.plans["p1"].last_run_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_stage,expected", [
    ("freeze", ["freeze"]),
    ("manifest", ["freeze", "manifest"]),
    ("collect", ["freeze", "manifest", "collect"]),
    ("submit", ["freeze", "manifest", "collect", "submit"]),
])
async def test_pipeline_failure_stops_following_stages(monkeypatch, failed_stage, expected):
    pipeline, repo, stages, create = _stages(monkeypatch, fail_at=failed_stage)
    row = await create()

    await pipeline.run_pipeline("p1", row.id, repository=repo)

    assert stages == expected
    assert (row.state, row.error_code) == ("FAILED", "RESOURCE_NOT_FOUND")
    assert repo.plans["p1"].consecutive_failures == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["freeze", "collect"])
async def test_pipeline_rechecks_cancellation_before_next_stage(monkeypatch, boundary):
    pipeline, repo, stages, create = _stages(monkeypatch, cancel_at=boundary)
    row = await create()

    await pipeline.run_pipeline("p1", row.id, repository=repo)

    assert row.state == "CANCELLED"
    assert "submit" not in stages
    assert stages == (["freeze"] if boundary == "freeze" else ["freeze", "manifest", "collect"])
    assert repo.plans["p1"].consecutive_failures == 0


@pytest.mark.asyncio
async def test_pipeline_rechecks_timeout_before_snapshot_submit(monkeypatch):
    pipeline, repo, stages, create = _stages(monkeypatch, expire_at="collect")
    row = await create()

    await pipeline.run_pipeline("p1", row.id, repository=repo)

    assert stages == ["freeze", "manifest", "collect"]
    assert (row.state, row.error_code) == ("FAILED", "COLLECTION_RUN_TIMEOUT")


@pytest.mark.asyncio
async def test_duplicate_launch_owns_one_pipeline_and_one_heartbeat(monkeypatch):
    pipeline, repo, stages, create = _stages(monkeypatch)
    row = await create()
    tasks = []

    def track(coro, *, run, run_model):
        assert run.id == row.id
        tasks.append(asyncio.create_task(coro))
        return tasks[-1]

    monkeypatch.setattr(pipeline, "start_tracked", track)
    await asyncio.gather(
        runs.launch_run("p1", row.id, repository=repo),
        runs.launch_run("p1", row.id, repository=repo),
    )
    await asyncio.gather(*tasks)

    assert len(tasks) == 1
    assert row.heartbeat_at.tzinfo is not None
    assert stages == ["freeze", "manifest", "collect", "submit"]
    assert row.state == "SUBMITTED"


@pytest.mark.asyncio
async def test_direct_launch_initialization_failure_terminalizes_run(monkeypatch):
    from services.network_topology.collection import pipeline

    repo = FakeRunRepository(plans=[plan("p1")])
    result = await runs.start_scheduled("p1", repository=repo, launch=False)
    row = repo.runs[result["collection_run_id"]]

    def cannot_schedule(coro, **kwargs):
        coro.close()
        raise RuntimeError("event loop closing")

    monkeypatch.setattr(pipeline, "start_tracked", cannot_schedule)
    with pytest.raises(RuntimeError, match="event loop closing"):
        await runs.launch_run("p1", row.id, repository=repo)
    assert (row.state, row.error_code) == ("FAILED", "COLLECTION_PIPELINE_START_FAILED")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PENDING", "MANIFEST_FROZEN", "COLLECTED"])
async def test_sweep_detects_lost_pipeline_outside_action_dispatch(state):
    repo = FakeRunRepository(plans=[plan("p1")])
    result = await runs.start_scheduled("p1", repository=repo, launch=False)
    row = repo.runs[result["collection_run_id"]]
    row.state = state
    row.heartbeat_at = runs._now() - timedelta(minutes=3)

    assert await runs.sweep_runs(repository=repo) == 1
    assert (row.state, row.error_code) == ("FAILED", "COLLECTION_WORKER_LOST")
    assert repo.plans["p1"].consecutive_failures == 1


@pytest.mark.asyncio
async def test_real_collection_stages_build_and_submit_a_valid_sanitized_snapshot(monkeypatch):
    """Keep freeze/manifest/dispatch/build/submit real; replace external storage and device I/O."""
    import orjson
    from services.network_topology import snapshot
    from services.network_topology.collection import dispatch, manifest_issue, pipeline, submit, targets
    from tests.network_topology.auto_collection_fakes import FakeTargetRepository

    item = SimpleNamespace(id="i1", app_name="device-app", app_version=None,
        action_func="collect", action_input={}, resources=[{"resource_id": "r1"}])
    repo = FakeRunRepository(plans=[plan("p1", concurrency=1, per_item_timeout_seconds=10, items=[item])])
    created = await runs.start_scheduled("p1", repository=repo, launch=False)
    row = repo.runs[created["collection_run_id"]]
    row.save = AsyncMock()
    target_repo = FakeTargetRepository(
        resources={"r1": {"name": "switch", "resources_json": {"host": {"value": "10.0.0.1"}}}},
        tools={"device-app": ("v1", "main.py")},
        actions={("device-app", "v1"): [{"func": "collect", "name": "Collect"}]},
    )
    stored = {}

    @asynccontextmanager
    async def transaction():
        yield object()

    class RunQuery:
        def using_db(self, _connection):
            return self

        def select_for_update(self):
            return self

        async def first(self):
            return row

    class ManifestQuery:
        async def first(self):
            return stored["manifest"]

    async def persist(_connection, authority, topology, profile, intent, provenance, target, registry):
        values = {key: value for key, value in target.items() if key != "expected_resources"}
        values["issued_at"] = datetime.fromisoformat(values["issued_at"])
        values["expires_at"] = datetime.fromisoformat(values["expires_at"])
        stored["manifest"] = SimpleNamespace(**values, expected_resources_json=orjson.dumps(target["expected_resources"]).decode())

    async def freeze(collection_plan, items):
        return await targets.freeze_targets(collection_plan, items, repository=target_repo)

    async def executor(tenant_id, target, *, timeout, executor_id):
        assert (tenant_id, target.resource_id, target.app_version, timeout) == ("t1", "r1", "v1", 10)
        return dispatch.ExecResult(0, '{"code":200,"password":"must-be-redacted","data":{"records":[]}}', 1)

    async def collect(collection_plan, run, *, repository):
        return await dispatch.collect_and_store(collection_plan, run, executor=executor, repository=repository)

    async def ingest(tenant_id, topology_id, actor, envelope, *, body_size, now=None):
        assert (tenant_id, topology_id, actor) == ("t1", "topo-1", f"collection-run:{row.id}")
        admitted = snapshot.validate_direct_snapshot(topology_id, envelope, body_size=body_size,
                                                     now=datetime.now(timezone.utc))
        assert admitted.payload_hash
        assert "must-be-redacted" not in orjson.dumps(envelope).decode()
        stored["snapshot"] = envelope
        return snapshot.SyncAccepted("analysis-1", "snapshot-1")

    monkeypatch.setattr(manifest_issue, "in_transaction", transaction)
    monkeypatch.setattr(manifest_issue, "TopologyCollectionRun", SimpleNamespace(filter=lambda **_: RunQuery()))
    monkeypatch.setattr(manifest_issue.manifest, "_locked_topology", AsyncMock(return_value=SimpleNamespace(
        topology_profile_revision=1, current_graph_revision=0, publication_revision=0)))
    monkeypatch.setattr(manifest_issue.manifest, "_current_profile", AsyncMock(return_value=(SimpleNamespace(revision=1), {})))
    monkeypatch.setattr(manifest_issue.manifest, "_upgrade_intent", AsyncMock(side_effect=lambda _c, _a, _t, clock: (None, clock())))
    monkeypatch.setattr(manifest_issue.manifest, "_next_sequence", AsyncMock(return_value=1))
    monkeypatch.setattr(manifest_issue.manifest, "_persist_manifest", persist)
    monkeypatch.setattr(dispatch, "TopologyCollectionManifest", SimpleNamespace(filter=lambda **_: ManifestQuery()))
    monkeypatch.setattr(pipeline, "freeze_targets", freeze)
    monkeypatch.setattr(pipeline, "collect_and_store", collect)
    monkeypatch.setattr(submit.topology_snapshot, "ingest_direct_snapshot", ingest)

    await pipeline.run_pipeline("p1", row.id, repository=repo)

    assert row.state == "SUBMITTED"
    assert (row.analysis_run_id, row.snapshot_id) == ("analysis-1", "snapshot-1")
    assert row.device_total == row.device_succeeded == 1
    assert stored["snapshot"]["completeness"]["collected_resource_ids"] == ["r1"]
    assert row.snapshot_draft is None
