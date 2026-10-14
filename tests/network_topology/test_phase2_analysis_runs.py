from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import orjson
import pytest

from models._network_topology_v2_base import (
    AnalysisKind,
    AnalysisRunStage,
    AnalysisRunState,
)
from services.network_topology import analysis_runs


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)


class MemoryRepository:
    def __init__(self, *, run=None, snapshot=None, topology=None, profile=None):
        self.run = run
        self.snapshot = snapshot
        self.topology = topology
        self.profile = profile or SimpleNamespace(id="profile-3", revision=3)
        self.created = []
        self.saved = []
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            yield "connection"

    async def get_run(self, tenant_id, sync_id, *, connection=None, lock=False):
        del connection, lock
        if self.run is None:
            return None
        if self.run.tenant_id != tenant_id or str(self.run.id) != sync_id:
            return None
        return self.run

    async def get_snapshot(self, tenant_id, topology_id, snapshot_id, *, connection):
        del connection
        if self.snapshot is None:
            return None
        if self.snapshot.tenant_id != tenant_id:
            return None
        if str(self.snapshot.topology_row_id) != str(topology_id):
            return None
        if self.snapshot.snapshot_id != snapshot_id:
            return None
        return self.snapshot

    async def get_topology(self, tenant_id, topology_id, *, connection, lock):
        del connection, lock
        if self.topology is None:
            return None
        if self.topology.tenant_id != tenant_id:
            return None
        if str(self.topology.id) != str(topology_id):
            return None
        return self.topology

    async def get_profile(
        self, tenant_id, topology_id, revision, *, connection, lock=False
    ):
        del connection, lock
        if self.topology is None or self.topology.tenant_id != tenant_id:
            return None
        if str(self.topology.id) != str(topology_id):
            return None
        if self.profile.revision != revision:
            return None
        return self.profile

    async def create_run(self, values, *, connection):
        del connection
        row = SimpleNamespace(id=f"run-{len(self.created) + 1}", **values)
        self.created.append(deepcopy(values))
        self.run = row
        return row

    async def save_run(self, run, fields, *, connection):
        del connection
        self.saved.append((run, tuple(fields)))


class BlockingSaveRepository(MemoryRepository):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cancellation_applied = asyncio.Event()
        self.finish_cancel = asyncio.Event()

    async def save_run(self, run, fields, *, connection):
        self.cancellation_applied.set()
        await self.finish_cancel.wait()
        await super().save_run(run, fields, connection=connection)

def _run(**overrides):
    values = {
        "id": "run-1",
        "tenant_id": "tenant-1",
        "state": AnalysisRunState.RUNNING,
        "stage": AnalysisRunStage.PARSING,
        "attempt_count": 2,
        "retryable": True,
        "next_retry_at": None,
        "error_code": None,
        "error_summary": "private traceback and raw model output",
        "blocked_by": None,
        "topology_row_id": "topology-1",
        "lease_generation": 4,
        "fencing_token": 8,
        "cancellation_requested": False,
        "cancellation_requested_by": None,
        "cancellation_requested_at": None,
        "cancellation_reason": None,
        "finished_at": None,
        "prompt_version": "private-prompt-v9",
        "raw_model_output": "secret model output",
        "config_text": "password customer-secret",
        "credentials": "token-value",
        "traceback": "internal stack",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _snapshot(**overrides):
    values = {
        "id": "snapshot-row-1",
        "snapshot_id": "snapshot-1",
        "tenant_id": "tenant-1",
        "schema_version": analysis_runs.COLLECTION_V2_SCHEMA,
        "topology_row_id": "topology-1",
        "profile_revision_row_id": "profile-3",
        "playbook_id": "playbook-1",
        "run_id": "collector-run-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _topology(**overrides):
    values = {
        "id": "topology-1",
        "tenant_id": "tenant-1",
        "current_graph_revision": 7,
        "publication_revision": 9,
        "topology_profile_revision": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _race_cancel_with_old_worker(repository, run, old_lease):
    cancel_task = asyncio.create_task(
        analysis_runs.cancel_analysis(
            "tenant-1",
            "run-1",
            actor="account-1",
            reason="operator requested stop",
            repository=repository,
            clock=lambda: NOW,
        )
    )
    await repository.cancellation_applied.wait()

    async def old_worker_write():
        async with repository.transaction():
            return analysis_runs.worker_write_allowed(run, *old_lease)

    worker_task = asyncio.create_task(old_worker_write())
    await asyncio.sleep(0)
    assert not worker_task.done()
    repository.finish_cancel.set()
    return await asyncio.gather(cancel_task, worker_task)


@pytest.mark.asyncio
async def test_status_is_tenant_scoped_and_returns_only_public_fields():
    repository = MemoryRepository(run=_run(), topology=_topology())

    status = await analysis_runs.get_status(
        "tenant-1", "run-1", repository=repository
    )

    assert status == {
        "sync_id": "run-1",
        "state": "RUNNING",
        "terminal": False,
        "stage": "PARSING",
        "blocked_by": None,
        "attempt": 2,
        "retryable": True,
        "next_retry_at": None,
        "published": False,
        "graph_revision": 7,
        "publication_revision": 9,
        "error_code": None,
        "error_summary": None,
    }
    analysis_runs.contracts.validate_contract(
        {"code": 0, "data": status},
        "aioe.network-topology.api/status-response/v1",
    )
    serialized = orjson.dumps(status)
    for secret in (
        b"private-prompt",
        b"secret model output",
        b"customer-secret",
        b"token-value",
        b"internal stack",
    ):
        assert secret not in serialized

    with pytest.raises(analysis_runs.AnalysisRunError) as cross_tenant:
        await analysis_runs.get_status(
            "tenant-2", "run-1", repository=repository
        )
    assert (cross_tenant.value.code, cross_tenant.value.status) == (
        analysis_runs.RUN_NOT_FOUND,
        404,
    )


@pytest.mark.asyncio
async def test_reanalyze_creates_independent_run_without_consuming_manifest():
    manifest = SimpleNamespace(status="COMMITTED", consumed_at=NOW)
    repository = MemoryRepository(snapshot=_snapshot(), topology=_topology())

    result = await analysis_runs.reanalyze_snapshot(
        "tenant-1",
        "snapshot-1",
        actor="account-1",
        topology_id="topology-1",
        target_profile_revision=3,
        publish_as_current=True,
        expected_current_graph_revision=7,
        expected_publication_revision=9,
        repository=repository,
        id_factory=lambda: "publication-attempt-1",
    )

    assert result["sync_id"] == "run-1"
    assert result == {
        "sync_id": "run-1",
        "snapshot_id": "snapshot-1",
        "status": "RECEIVED",
        "status_url": "/api/network-topology/config-sync/status",
    }
    analysis_runs.contracts.validate_contract(
        {"code": 0, "data": result},
        "aioe.network-topology.api/reanalyze-response/v1",
    )
    created = repository.created[0]
    assert created["analysis_kind"] == AnalysisKind.REANALYZE
    assert created["snapshot_row_id"] == "snapshot-row-1"
    assert created["target_profile_revision_row_id"] == "profile-3"
    assert created["expected_current_graph_revision"] == 7
    assert created["expected_publication_revision"] == 9
    assert created["publication_attempt_id"] == "publication-attempt-1"
    assert manifest.status == "COMMITTED"
    assert manifest.consumed_at == NOW


@pytest.mark.asyncio
async def test_reanalyze_rejects_non_v2_and_cas_mismatch_before_enqueue():
    repository = MemoryRepository(
        snapshot=_snapshot(schema_version="legacy/v1"), topology=_topology()
    )
    with pytest.raises(analysis_runs.AnalysisRunError) as schema_error:
        await analysis_runs.reanalyze_snapshot(
            "tenant-1",
            "snapshot-1",
            actor="account-1",
            topology_id="topology-1",
            target_profile_revision=3,
            publish_as_current=True,
            expected_current_graph_revision=7,
            expected_publication_revision=9,
            repository=repository,
        )
    assert schema_error.value.code == analysis_runs.RUN_SNAPSHOT_SCHEMA_INVALID
    assert repository.created == []

    repository.snapshot.schema_version = analysis_runs.COLLECTION_V2_SCHEMA
    repository.topology.current_graph_revision = 8
    with pytest.raises(analysis_runs.AnalysisRunError) as stale:
        await analysis_runs.reanalyze_snapshot(
            "tenant-1",
            "snapshot-1",
            actor="account-1",
            topology_id="topology-1",
            target_profile_revision=3,
            publish_as_current=True,
            expected_current_graph_revision=7,
            expected_publication_revision=9,
            repository=repository,
        )
    assert (stale.value.code, stale.value.status) == (
        analysis_runs.RUN_CAS_MISMATCH,
        409,
    )
    assert repository.created == []


@pytest.mark.asyncio
async def test_cancel_fences_inflight_worker_and_repeated_cancel_is_idempotent():
    run = _run()
    repository = BlockingSaveRepository(run=run)
    old_lease = (run.lease_generation, run.fencing_token)

    first, worker_allowed = await _race_cancel_with_old_worker(
        repository, run, old_lease
    )
    second = await analysis_runs.cancel_analysis(
        "tenant-1",
        "run-1",
        actor="account-1",
        reason="must not replace original",
        repository=repository,
        clock=lambda: NOW + timedelta(seconds=1),
    )

    assert first == second == {
        "sync_id": "run-1",
        "state": "CANCELLED",
        "cancellation_requested": True,
    }
    assert run.lease_generation == old_lease[0] + 1
    assert run.fencing_token == old_lease[1] + 1
    assert run.cancellation_requested_by == "account-1"
    assert run.cancellation_reason == "operator requested stop"
    assert len(repository.saved) == 1
    assert worker_allowed is False
    analysis_runs.contracts.validate_contract(
        {"code": 0, "data": first},
        "aioe.network-topology.api/cancel-response/v1",
    )


@pytest.mark.asyncio
async def test_cancel_loses_publish_commit_race_without_mutating_run():
    run = _run(state=AnalysisRunState.RUNNING)
    repository = MemoryRepository(run=run)
    publish_locked = asyncio.Event()
    finish_publish = asyncio.Event()

    async def publish_commit():
        async with repository.transaction():
            run.stage = AnalysisRunStage.PUBLISHING
            publish_locked.set()
            await finish_publish.wait()

    publish_task = asyncio.create_task(publish_commit())
    await publish_locked.wait()
    cancel_task = asyncio.create_task(
        analysis_runs.cancel_analysis(
            "tenant-1",
            "run-1",
            actor="account-1",
            reason="too late",
            repository=repository,
        )
    )
    await asyncio.sleep(0)
    assert not cancel_task.done()
    finish_publish.set()
    await publish_task

    with pytest.raises(analysis_runs.AnalysisRunError) as conflict:
        await cancel_task

    assert (conflict.value.code, conflict.value.status) == (
        analysis_runs.RUN_PUBLISH_COMMIT_STARTED,
        409,
    )
    assert run.state == AnalysisRunState.RUNNING
    assert run.cancellation_requested is False
    assert repository.saved == []


@pytest.mark.asyncio
async def test_terminal_cancel_returns_original_status_without_new_write():
    run = _run(
        state=AnalysisRunState.SUCCEEDED,
        stage=AnalysisRunStage.PUBLISHING,
        retryable=False,
    )
    repository = MemoryRepository(run=run)

    result = await analysis_runs.cancel_analysis(
        "tenant-1",
        "run-1",
        actor="account-1",
        reason="already done",
        repository=repository,
    )

    assert result["state"] == "SUCCEEDED"
    assert result == {
        "sync_id": "run-1",
        "state": "SUCCEEDED",
        "cancellation_requested": True,
    }
    assert repository.saved == []


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        ("PROVIDER_TIMEOUT", True),
        ("TEMPORARY_NETWORK_FAILURE", True),
        ("TEMPORARY_DATABASE_FAILURE", True),
        ("WORKER_LEASE_LOST", True),
        ("SCHEMA_INVALID", False),
        ("EVIDENCE_INVALID", False),
        ("STALE_GRAPH_REVISION", False),
        ("TENANT_INVALID", False),
        ("PERMISSION_DENIED", False),
        ("UNRECOGNIZED_INTERNAL_ERROR", False),
    ],
)
def test_error_retryability_is_frozen_and_unknown_codes_fail_closed(code, retryable):
    assert analysis_runs.classify_error(code).retryable is retryable


@pytest.mark.asyncio
async def test_worker_crash_can_retry_but_old_crashed_lease_cannot_write():
    run = _run(
        state=AnalysisRunState.RETRY_WAIT,
        error_code="WORKER_LEASE_LOST",
        error_summary="worker process crashed with internal stack",
        next_retry_at=NOW + timedelta(minutes=1),
    )
    repository = MemoryRepository(run=run, topology=_topology())
    crashed_lease = (run.lease_generation, run.fencing_token)

    run.lease_generation += 1
    run.fencing_token += 1
    status = await analysis_runs.get_status(
        "tenant-1", "run-1", repository=repository
    )

    assert status["state"] == "RETRY_WAIT"
    assert status["retryable"] is True
    assert status["terminal"] is False
    assert status["next_retry_at"] == "2026-07-30T08:01:00Z"
    assert not analysis_runs.worker_write_allowed(run, *crashed_lease)


def _request(body):
    return SimpleNamespace(
        json=body,
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "sub": "account-1"}),
        path="/network-topology/status",
    )


@pytest.mark.asyncio
async def test_thin_routes_use_expected_permissions_and_safe_error_body(monkeypatch):
    from libs.security import authorization
    from services.network_topology import views

    permissions = []

    async def allow(_request, *required, refresh=False):
        permissions.append((required, refresh))
        return True

    async def status(*_args, **_kwargs):
        return {"sync_id": "run-1", "state": "RUNNING"}

    async def fail(*_args, **_kwargs):
        raise analysis_runs.AnalysisRunError(analysis_runs.RUN_CAS_MISMATCH)

    monkeypatch.setattr(authorization, "check_permissions", allow)
    monkeypatch.setattr(views.analysis_runs, "get_status", status)
    response = await views.analysis_status(_request({"sync_id": "run-1"}))
    assert orjson.loads(response.body)["data"]["sync_id"] == "run-1"

    monkeypatch.setattr(views.analysis_runs, "reanalyze_snapshot", fail)
    response = await views.reanalyze_snapshot(
        _request(
            {
                "snapshot_id": "snapshot-1",
                "id": "topology-1",
                "target_profile_revision": 3,
                "publish_as_current": True,
                "expected_current_graph_revision": 7,
                "expected_publication_revision": 9,
            }
        )
    )
    payload = orjson.loads(response.body)
    assert response.status == 409
    assert payload["data"] == {
        "error_code": analysis_runs.RUN_CAS_MISMATCH,
        "retryable": False,
    }
    assert "traceback" not in orjson.dumps(payload).decode().lower()

    assert permissions == [
        (("network_topology:sync", "network_topology:reanalyze"), False),
        (("network_topology:reanalyze",), False),
    ]
    assert inspect.iscoroutinefunction(inspect.unwrap(views.cancel_analysis))
