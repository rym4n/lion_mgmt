from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import settings
from models._network_topology_v2_base import (
    AnalysisRunStage,
    AnalysisRunState,
    PayloadObjectStatus,
)
from services.network_topology import alerts, leases, worker


NOW = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)


def _run(**overrides):
    values = {
        "id": "sync-1",
        "tenant_id": "tenant-1",
        "topology_row_id": "topology-1",
        "snapshot_row_id": "snapshot-1",
        "target_profile_revision_row_id": "profile-3",
        "analysis_kind": "SNAPSHOT",
        "state": AnalysisRunState.RECEIVED,
        "stage": AnalysisRunStage.SPLITTING,
        "attempt_count": 0,
        "max_attempts": 3,
        "next_retry_at": None,
        "lease_owner": None,
        "lease_expires_at": None,
        "heartbeat_at": None,
        "lease_generation": 0,
        "fencing_token": 0,
        "started_at": None,
        "finished_at": None,
        "error_code": None,
        "error_summary": None,
        "retryable": True,
        "blocked_by": None,
        "cancellation_requested": False,
        "expected_current_graph_revision": 7,
        "expected_publication_revision": 9,
        "publish_as_current": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _snapshot(**overrides):
    values = {
        "id": "snapshot-1",
        "tenant_id": "tenant-1",
        "topology_row_id": "topology-1",
        "profile_revision": 3,
        "collection_sequence": 12,
        "redacted_payload": "{\"schema_version\":\"collection/v2\"}",
        "payload_object_row_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _topology(**overrides):
    values = {
        "id": "topology-1",
        "tenant_id": "tenant-1",
        "topology_profile_revision": 3,
        "current_graph_revision": 7,
        "publication_revision": 9,
        "last_published_collection_sequence": 11,
        "current_snapshot_row_id": "older-snapshot",
        "current_analysis_run_row_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class MemoryLeaseRepository:
    def __init__(self, runs, snapshots=None, topology=None, payload_statuses=None):
        self.runs = runs
        self.snapshots = snapshots or {"snapshot-1": _snapshot()}
        self.topology = topology or _topology()
        self.payload_statuses = payload_statuses or {}
        self.lock = asyncio.Lock()
        self.saved = []

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            yield "connection"

    async def claim_candidates(self, now, limit, *, connection):
        del connection
        return [
            item
            for item in self.runs[:limit]
            if leases.run_is_due(item, now, publish_enabled=True)
        ]

    async def payload_ready(self, run, *, connection):
        del connection
        snapshot = self.snapshots[run.snapshot_row_id]
        if snapshot.redacted_payload is not None:
            return True
        return self.payload_statuses.get(snapshot.payload_object_row_id) == PayloadObjectStatus.LIVE

    async def save_run(self, run, fields, *, connection):
        del connection
        self.saved.append((run.id, tuple(fields)))

    async def get_guard_context(self, run, *, connection):
        del connection
        return leases.GuardContext(
            topology=self.topology,
            snapshot=self.snapshots[run.snapshot_row_id],
            profile=SimpleNamespace(id="profile-3", revision=3),
            current_snapshot=None,
        )


@pytest.mark.asyncio
async def test_claim_requires_committed_inline_or_live_payload_and_fences():
    ready = _run(id="ready", snapshot_row_id="ready-snapshot")
    staging = _run(id="staging", snapshot_row_id="staging-snapshot")
    live = _run(id="live", snapshot_row_id="live-snapshot")
    snapshots = {
        "ready-snapshot": _snapshot(id="ready-snapshot", redacted_payload=None, payload_object_row_id="object-ready"),
        "staging-snapshot": _snapshot(id="staging-snapshot", redacted_payload=None, payload_object_row_id="object-staging"),
        "live-snapshot": _snapshot(id="live-snapshot", redacted_payload=None, payload_object_row_id="object-live"),
    }
    repository = MemoryLeaseRepository(
        [ready, staging, live],
        snapshots=snapshots,
        payload_statuses={
            "object-ready": PayloadObjectStatus.READY,
            "object-staging": PayloadObjectStatus.STAGING,
            "object-live": PayloadObjectStatus.LIVE,
        },
    )

    claimed = await leases.claim_due_run(
        "worker-a", repository=repository, clock=lambda: NOW
    )

    assert claimed.run.id == "live"
    assert claimed.identity.lease_generation == 1
    assert claimed.identity.fencing_token > 0
    assert live.attempt_count == 1
    assert ready.state == AnalysisRunState.RECEIVED
    assert staging.state == AnalysisRunState.RECEIVED


def test_state_machine_rejects_fact_validation_bypass_to_publishing():
    assert leases.stage_transition_allowed(
        AnalysisRunStage.PARSING, AnalysisRunStage.VALIDATING_FACTS
    )
    assert not leases.stage_transition_allowed(
        AnalysisRunStage.PARSING, AnalysisRunStage.PUBLISHING
    )
    with pytest.raises(leases.InvalidTransition):
        leases.require_stage_transition(
            AnalysisRunStage.FACTS_READY, AnalysisRunStage.PUBLISHING
        )


@pytest.mark.asyncio
async def test_stale_fencing_token_cannot_heartbeat_or_write_stage():
    run = _run(state=AnalysisRunState.RUNNING, lease_generation=4, fencing_token=40)
    repository = MemoryLeaseRepository([run])
    stale = leases.LeaseIdentity("sync-1", "worker-old", 3, 30)

    assert not await leases.heartbeat(stale, repository=repository, clock=lambda: NOW)
    assert not await leases.advance_stage(
        stale,
        AnalysisRunStage.PARSING,
        repository=repository,
        clock=lambda: NOW,
    )
    assert run.stage == AnalysisRunStage.SPLITTING
    run.stage = AnalysisRunStage.PUBLISHING
    assert not await leases.publication_allowed(
        stale, repository=repository, clock=lambda: NOW
    )
    assert not await leases.schedule_retry(
        stale,
        "TEMPORARY_DATABASE_FAILURE",
        repository=repository,
        clock=lambda: NOW,
    )
    assert not await leases.mark_succeeded(
        stale, repository=repository, clock=lambda: NOW
    )


@pytest.mark.asyncio
async def test_publication_guard_supersedes_current_fence_after_cas_changes():
    run = _run(
        state=AnalysisRunState.RUNNING,
        stage=AnalysisRunStage.PUBLISHING,
        lease_generation=2,
        fencing_token=20,
    )
    repository = MemoryLeaseRepository(
        [run], topology=_topology(current_graph_revision=8)
    )
    identity = leases.LeaseIdentity("sync-1", "worker-a", 2, 20)

    assert not await leases.publication_allowed(
        identity, repository=repository, clock=lambda: NOW
    )
    assert run.state == AnalysisRunState.SUPERSEDED
    assert run.error_code == "PUBLICATION_CAS_MISMATCH"


@pytest.mark.parametrize(
    "stage",
    (
        AnalysisRunStage.SPLITTING,
        AnalysisRunStage.PARSING,
        AnalysisRunStage.FACTS_READY,
        AnalysisRunStage.VALIDATING_GRAPH,
    ),
    ids=("after-claim", "during-parse", "after-facts", "before-publish"),
)
@pytest.mark.asyncio
async def test_kill_points_resume_from_durable_stage_with_new_fence(stage):
    run = _run(stage=stage)
    repository = MemoryLeaseRepository([run])
    first = await leases.claim_due_run(
        "worker-old", repository=repository, clock=lambda: NOW
    )
    old_identity = deepcopy(first.identity)

    run.lease_expires_at = NOW - timedelta(seconds=1)
    recovered = await leases.claim_due_run(
        "worker-new", repository=repository, clock=lambda: NOW
    )

    assert recovered.run.stage == stage
    assert recovered.run.state == AnalysisRunState.RUNNING
    assert recovered.identity.lease_generation == old_identity.lease_generation + 1
    assert recovered.identity.fencing_token != old_identity.fencing_token
    assert not await leases.advance_stage(
        old_identity,
        leases.next_stage(stage),
        repository=repository,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_retry_uses_exponential_backoff_and_max_attempt_is_terminal():
    run = _run(
        state=AnalysisRunState.RUNNING,
        attempt_count=2,
        max_attempts=3,
        lease_generation=2,
        fencing_token=20,
    )
    repository = MemoryLeaseRepository([run])
    identity = leases.LeaseIdentity("sync-1", "worker-a", 2, 20)

    assert await leases.schedule_retry(
        identity,
        "TEMPORARY_DATABASE_FAILURE",
        repository=repository,
        clock=lambda: NOW,
        base_delay=timedelta(seconds=5),
    )
    assert run.state == AnalysisRunState.RETRY_WAIT
    assert run.next_retry_at == NOW + timedelta(seconds=10)

    run.state = AnalysisRunState.RUNNING
    run.attempt_count = 3
    run.lease_owner = "worker-a"
    assert await leases.schedule_retry(
        identity,
        "TEMPORARY_DATABASE_FAILURE",
        repository=repository,
        clock=lambda: NOW,
    )
    assert run.state == AnalysisRunState.FAILED
    assert run.finished_at == NOW


@pytest.mark.asyncio
async def test_reclaim_revalidates_profile_sequence_revisions_and_cancellation():
    cases = (
        ({"cancellation_requested": True}, {}, AnalysisRunState.CANCELLED),
        ({}, {"topology_profile_revision": 4}, AnalysisRunState.SUPERSEDED),
        ({}, {"last_published_collection_sequence": 13}, AnalysisRunState.SUPERSEDED),
        ({}, {"current_graph_revision": 8}, AnalysisRunState.SUPERSEDED),
        ({}, {"publication_revision": 10}, AnalysisRunState.SUPERSEDED),
    )
    for run_changes, topology_changes, expected in cases:
        run = _run(**run_changes)
        repository = MemoryLeaseRepository([run], topology=_topology(**topology_changes))
        claimed = await leases.claim_due_run(
            "worker-a", repository=repository, clock=lambda: NOW
        )
        assert claimed is None
        assert run.state == expected


class MemoryAlertRepository:
    def __init__(self):
        self.rows = {}

    async def get_or_create(self, run, values):
        key = (run.tenant_id, run.id, alerts.WORKER_UNAVAILABLE)
        created = key not in self.rows
        self.rows.setdefault(key, SimpleNamespace(**values))
        return self.rows[key], created

    async def save(self, row, fields):
        row.saved_fields = tuple(fields)

    async def find_open(self, run):
        return self.rows.get((run.tenant_id, run.id, alerts.WORKER_UNAVAILABLE))


@pytest.mark.asyncio
async def test_worker_unavailable_alert_is_idempotent_and_closed_on_claim():
    run = _run()
    repository = MemoryAlertRepository()

    first = await alerts.open_worker_unavailable(run, repository=repository, clock=lambda: NOW)
    second = await alerts.open_worker_unavailable(run, repository=repository, clock=lambda: NOW)
    await alerts.close_worker_unavailable(
        run, "worker-a", repository=repository, clock=lambda: NOW
    )

    assert first is second
    assert len(repository.rows) == 1
    assert first.status == "CLOSED"
    assert first.closed_by == "worker-a"


class RecordingPipeline:
    def __init__(self):
        self.calls = []

    async def execute_stage(self, run, stage, lease):
        self.calls.append((run.id, stage, lease.fencing_token))
        return worker.StageResult()


@pytest.mark.asyncio
async def test_database_polling_claims_without_redis_wakeup(monkeypatch):
    monkeypatch.setattr(settings, "TOPOLOGY_V2_ANALYSIS_ENABLED", True)
    run = _run(stage=AnalysisRunStage.SPLITTING)
    repository = MemoryLeaseRepository([run])
    pipeline = RecordingPipeline()
    service = worker.TopologyAnalysisWorker(
        worker_id="worker-a",
        pipeline=pipeline,
        lease_repository=repository,
        alert_repository=MemoryAlertRepository(),
        clock=lambda: NOW,
    )

    assert await service.run_once()
    assert pipeline.calls[0][:2] == ("sync-1", AnalysisRunStage.SPLITTING)


@pytest.mark.asyncio
async def test_publish_requires_explicit_pipeline_confirmation(monkeypatch):
    monkeypatch.setattr(settings, "TOPOLOGY_V2_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(settings, "TOPOLOGY_V2_PUBLISH_ENABLED", True)
    run = _run()
    service = worker.TopologyAnalysisWorker(
        worker_id="worker-a",
        pipeline=RecordingPipeline(),
        lease_repository=MemoryLeaseRepository([run]),
        alert_repository=MemoryAlertRepository(),
        clock=lambda: NOW,
    )

    assert await service.run_once()
    assert run.state == AnalysisRunState.FAILED
    assert run.error_code == "GRAPH_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_graceful_shutdown_releases_lease_without_faking_success():
    run = _run(
        state=AnalysisRunState.RUNNING,
        lease_owner="worker-a",
        lease_expires_at=NOW + timedelta(seconds=60),
        heartbeat_at=NOW,
        lease_generation=2,
        fencing_token=20,
    )
    repository = MemoryLeaseRepository([run])
    service = worker.TopologyAnalysisWorker(
        worker_id="worker-a",
        pipeline=RecordingPipeline(),
        lease_repository=repository,
        alert_repository=MemoryAlertRepository(),
        clock=lambda: NOW,
    )
    service.active_lease = leases.LeaseIdentity("sync-1", "worker-a", 2, 20)

    await service.shutdown()

    assert run.state == AnalysisRunState.RUNNING
    assert run.lease_owner is None
    assert run.finished_at is None


@pytest.mark.asyncio
async def test_disabled_worker_opens_idempotent_unavailable_alert(monkeypatch):
    monkeypatch.setattr(settings, "TOPOLOGY_V2_ANALYSIS_ENABLED", False)
    run = _run()
    alert_repository = MemoryAlertRepository()
    service = worker.TopologyAnalysisWorker(
        worker_id="worker-a",
        pipeline=RecordingPipeline(),
        lease_repository=MemoryLeaseRepository([run]),
        alert_repository=alert_repository,
        clock=lambda: NOW,
    )

    assert not await service.run_once()
    assert not await service.run_once()
    assert len(alert_repository.rows) == 1
    assert next(iter(alert_repository.rows.values())).status == "OPEN"


def test_worker_is_independent_entrypoint_and_pipeline_protocol_is_explicit():
    assert callable(worker.main)
    assert worker.TopologyAnalysisPipeline.execute_stage.__annotations__ == {
        "run": "Any",
        "stage": "AnalysisRunStage",
        "lease": "LeaseIdentity",
        "return": "StageResult",
    }
    assert "sanic" not in worker.__dict__
