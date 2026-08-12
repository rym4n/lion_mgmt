from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import inspect

import orjson
import pytest

import settings
from models._network_topology_v2_base import UploadSessionStatus
from services.network_topology.payload_store import prepare_stored_envelope
from services.network_topology.snapshot import ingest_direct_snapshot
from services.network_topology.upload import (
    UploadAuthority,
    UploadOwner,
    commit_upload_session,
    stage_chunk,
    start_upload,
)
from tests.network_topology.test_phase2_snapshot_direct import (
    NOW,
    _PayloadStore,
    _Repository,
    _envelope,
)
from tests.network_topology.test_phase2_upload_session import MemoryRepository


@pytest.fixture(autouse=True)
def _enable_ingress(monkeypatch):
    monkeypatch.setattr(settings, "TOPOLOGY_V2_INGRESS_ENABLED", True)


def _start_request(envelope):
    frozen = deepcopy(envelope)
    for source in frozen["sources"]:
        for resource in source["resources"]:
            observations = resource["result"].pop("observations")
            resource["result"]["expected_observation_count"] = len(observations)
    return {
        "id": "topology-1",
        "manifest_id": envelope["target_manifest"]["manifest_id"],
        "snapshot_envelope_without_observations": frozen,
        "client_expected_input_hash": prepare_stored_envelope(envelope).payload_hash,
    }


def _business_snapshot(row):
    ignored = {"id", "payload_object_row_id", "target_manifest_row_id"}
    return {key: value for key, value in vars(row).items() if key not in ignored}


async def _chunk_commit(envelope, snapshot_repository, store, upload_repository=None):
    authority = UploadAuthority(
        tenant_id="tenant-1",
        topology_id="topology-1",
        subject="account-1",
        playbook_id=envelope["collector"]["playbook_id"],
        run_id=envelope["collector"]["run_id"],
    )
    owner = UploadOwner(authority.tenant_id, authority.subject)
    manifest = snapshot_repository.state["manifest"]
    manifest.issued_at = datetime.fromisoformat(
        envelope["target_manifest"]["issued_at"].replace("Z", "+00:00")
    )
    manifest.expires_at = datetime.fromisoformat(
        envelope["target_manifest"]["expires_at"].replace("Z", "+00:00")
    )
    upload_repository = upload_repository or MemoryRepository(manifest)
    request = _start_request(envelope)
    await start_upload(
        authority,
        request,
        repository=upload_repository,
        clock=lambda: NOW,
        id_factory=lambda: "upload-1",
    )
    observations = envelope["sources"][0]["resources"][0]["result"]["observations"]
    for observation in reversed(observations):
        await stage_chunk(
            owner,
            {
                "upload_session_id": "upload-1",
                "resource_id": "synthetic-router-1",
                "observation": deepcopy(observation),
            },
            repository=upload_repository,
            clock=lambda: NOW,
        )
    result = await commit_upload_session(
        owner,
        "upload-1",
        expected_payload_hash=request["client_expected_input_hash"],
        repository=upload_repository,
        snapshot_repository=snapshot_repository,
        store=store,
        clock=lambda: NOW,
    )
    return result, upload_repository


class _AtomicMemoryRepository(MemoryRepository):
    def __init__(self, manifest, snapshot_repository):
        super().__init__(manifest)
        self.snapshot_repository = snapshot_repository
        snapshot_repository.state["upload_sessions"] = self.sessions

    async def lock_session(self, owner, upload_id, connection):
        rows = (
            connection.pending["upload_sessions"]
            if hasattr(connection, "pending")
            else self.sessions
        )
        row = rows.get(upload_id)
        if row is None or row.deleted:
            return None
        return row if (row.tenant_id, row.submitter_subject) == (
            owner.tenant_id,
            owner.subject,
        ) else None


@pytest.mark.asyncio
async def test_direct_and_reordered_chunks_have_same_hash_and_equivalent_snapshot():
    envelope = _envelope()
    authoritative_hash = prepare_stored_envelope(envelope).payload_hash
    direct_repository = _Repository(envelope)
    direct = await ingest_direct_snapshot(
        "tenant-1",
        "topology-1",
        "account-1",
        envelope,
        body_size=len(orjson.dumps(envelope)),
        repository=direct_repository,
        store=_PayloadStore(repository=direct_repository),
        now=NOW,
    )
    chunk_repository = _Repository(envelope)
    chunk, uploads = await _chunk_commit(
        envelope,
        chunk_repository,
        _PayloadStore(repository=chunk_repository),
    )

    direct_snapshot = next(iter(direct_repository.state["snapshots"].values()))
    chunk_snapshot = next(iter(chunk_repository.state["snapshots"].values()))
    session = uploads.sessions["upload-1"]
    assert direct_snapshot.payload_hash == chunk_snapshot.payload_hash == authoritative_hash
    assert _business_snapshot(direct_snapshot) == _business_snapshot(chunk_snapshot)
    assert direct.snapshot_id == chunk.snapshot_id == envelope["snapshot_id"]
    assert session.authoritative_payload_hash == authoritative_hash
    assert session.status == UploadSessionStatus.COMMITTED


@pytest.mark.asyncio
async def test_same_upload_commit_retry_returns_one_snapshot_run_and_manifest_binding():
    envelope = _envelope()
    repository = _Repository(envelope)
    result, uploads = await _chunk_commit(
        envelope, repository, _PayloadStore(repository=repository)
    )
    retry = await commit_upload_session(
        UploadOwner("tenant-1", "account-1"),
        "upload-1",
        expected_payload_hash=prepare_stored_envelope(envelope).payload_hash,
        repository=uploads,
        snapshot_repository=repository,
        store=_PayloadStore(repository=repository),
        clock=lambda: NOW,
    )

    assert retry == result
    assert len(repository.state["snapshots"]) == 1
    assert len(repository.state["runs"]) == 1
    manifest = repository.state["manifest"]
    assert manifest.committed_payload_hash == uploads.sessions[
        "upload-1"
    ].authoritative_payload_hash


@pytest.mark.asyncio
async def test_final_transaction_failure_keeps_manifest_run_and_upload_consistent():
    envelope = _envelope()
    repository = _Repository(envelope, fail_commit=True)
    manifest = repository.state["manifest"]
    manifest.issued_at = NOW.replace(minute=0)
    manifest.expires_at = NOW.replace(minute=5)
    uploads = _AtomicMemoryRepository(manifest, repository)
    with pytest.raises(RuntimeError, match="DB commit"):
        await _chunk_commit(
            envelope,
            repository,
            _PayloadStore(repository=repository),
            upload_repository=uploads,
        )

    session = repository.state["upload_sessions"]["upload-1"]
    assert session.status == UploadSessionStatus.COMMITTING
    assert repository.state["manifest"].status.value == "ISSUED"
    assert repository.state["snapshots"] == repository.state["runs"] == {}

    result = await commit_upload_session(
        UploadOwner("tenant-1", "account-1"),
        "upload-1",
        expected_payload_hash=prepare_stored_envelope(envelope).payload_hash,
        repository=uploads,
        snapshot_repository=repository,
        store=_PayloadStore(repository=repository),
        clock=lambda: NOW,
    )
    assert result.sync_id == "sync-1"
    assert repository.state["upload_sessions"][
        "upload-1"
    ].status == UploadSessionStatus.COMMITTED


@pytest.mark.asyncio
async def test_legacy_config_sync_remains_http_200(monkeypatch):
    from services.network_topology import views
    from tests.test_network_topology_contract import (
        _TopologyRow,
        _async_true,
        _request,
    )

    config = {"devices": [{"hostname": "core"}]}
    canonical = orjson.dumps(config, option=orjson.OPT_SORT_KEYS)
    row = _TopologyRow(
        id="topology-1",
        name="legacy",
        source_config="stored",
        source_hash=hashlib.sha256(canonical).hexdigest(),
        topology_json={"nodes": [], "edges": []},
        layout_json={},
        revision=4,
        last_sync_at=None,
    )

    async def tenant_topology(*args, **kwargs):
        del args, kwargs
        return row

    monkeypatch.setattr(settings, "TOPOLOGY_LEGACY_SYNC_ENABLED", True)
    monkeypatch.setattr(views, "_tenant_topology", tenant_topology)
    monkeypatch.setattr(views, "check_permissions", lambda *_args: _async_true())
    response = await inspect.unwrap(views.sync_topology_config)(
        _request({"id": "topology-1", "config": config})
    )
    assert response.status == 200
    assert orjson.loads(response.body)["data"]["deprecated"] is True
