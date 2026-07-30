from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import inspect
from types import SimpleNamespace

import orjson
import pytest

import settings
from models._network_topology_v2_base import ManifestStatus, UploadSessionStatus
from services.network_topology import contracts
from services.network_topology.canonicalization import (
    canonicalize_and_hash_manifest,
    canonicalize_json,
    sha256_uri,
)
from services.network_topology.upload import (
    UPLOAD_CONFLICT,
    UPLOAD_EXPIRED,
    UPLOAD_NOT_FOUND,
    UPLOAD_STATE_INVALID,
    UploadAuthority,
    UploadError,
    UploadOwner,
    abort_upload_session,
    commit_upload_session,
    stage_chunk,
    start_upload,
    sweep_upload_sessions,
)

NOW = datetime(2026, 7, 30, tzinfo=UTC)
AUTHORITY = UploadAuthority(
    tenant_id="tenant-1",
    topology_id="topology-1",
    subject="user-1",
    playbook_id="synthetic-playbook",
    run_id="synthetic-run-1",
)


@pytest.fixture(autouse=True)
def _enable_ingress(monkeypatch):
    monkeypatch.setattr(settings, "TOPOLOGY_V2_INGRESS_ENABLED", True)


class MemoryRepository:
    def __init__(self, manifest):
        self.manifest = manifest
        self.sessions = {}
        self.staging = {}

    @asynccontextmanager
    async def transaction(self):
        yield object()

    async def lock_manifest(self, authority, manifest_id, connection):
        del connection
        if (
            self.manifest.tenant_id != authority.tenant_id
            or self.manifest.topology_row_id != authority.topology_id
            or self.manifest.manifest_id != manifest_id
        ):
            return None
        return self.manifest

    async def create_session(self, connection, **values):
        del connection
        row = SimpleNamespace(id=f"row-{len(self.sessions) + 1}", deleted=False, **values)
        self.sessions[row.upload_id] = row
        return row

    async def lock_session(self, owner, upload_id, connection):
        del connection
        row = self.sessions.get(upload_id)
        if row is None or row.deleted:
            return None
        actual = (
            row.tenant_id,
            row.submitter_subject,
        )
        expected = (owner.tenant_id, owner.subject)
        return row if actual == expected else None

    async def find_staging(self, tenant_id, session_row_id, observation_id, connection):
        del connection
        return self.staging.get((tenant_id, session_row_id, observation_id))

    async def create_staging(self, connection, **values):
        del connection
        row = SimpleNamespace(id=f"stage-{len(self.staging) + 1}", deleted=False, **values)
        key = (row.tenant_id, row.upload_session_row_id, row.observation_id)
        self.staging[key] = row
        return row

    async def save_session(self, row, connection):
        del row, connection

    async def list_staging(self, tenant_id, session_row_id):
        return [
            row
            for row in self.staging.values()
            if row.tenant_id == tenant_id
            and row.upload_session_row_id == session_row_id
            and not row.deleted
        ]

    async def cleanup_sessions(self, before, limit):
        candidates = [
            row
            for row in self.sessions.values()
            if not row.deleted
            and row.status != UploadSessionStatus.COMMITTED
            and (
                row.status in {UploadSessionStatus.ABORTED, UploadSessionStatus.EXPIRED}
                or (row.status == UploadSessionStatus.OPEN and row.expires_at <= before)
            )
        ]
        rows = candidates[:limit]
        expired = 0
        for row in rows:
            if row.status == UploadSessionStatus.OPEN:
                row.status = UploadSessionStatus.EXPIRED
                expired += 1
        return [row.id for row in rows], expired

    async def cleanup_staging(self, session_row_ids, limit):
        rows = [
            row
            for row in self.staging.values()
            if not row.deleted and row.upload_session_row_id in session_row_ids
        ][:limit]
        for row in rows:
            row.deleted = True
        return len(rows)


def _collection_example():
    envelope = contracts.load_schema("aioe.network-topology.collection/v2")["examples"][0]
    envelope["collection_started_at"] = "2026-07-30T00:01:00Z"
    envelope["collection_ended_at"] = "2026-07-30T00:02:00Z"
    for source in envelope["sources"]:
        for resource in source["resources"]:
            for observation in resource["result"]["observations"]:
                observation["observed_at"] = "2026-07-30T00:01:30Z"
    target = envelope["target_manifest"]
    target["issued_at"] = "2026-07-30T00:00:00Z"
    target["expires_at"] = "2026-07-30T00:05:00Z"
    target.pop("manifest_hash")
    target["manifest_hash"] = canonicalize_and_hash_manifest(target).manifest_hash
    return envelope


def _start_payload():
    envelope = deepcopy(_collection_example())
    for source in envelope["sources"]:
        for resource in source["resources"]:
            observations = resource["result"].pop("observations")
            resource["result"]["expected_observation_count"] = len(observations)
    return {
        "id": "topology-1",
        "manifest_id": "manifest-1",
        "snapshot_envelope_without_observations": envelope,
        "client_expected_input_hash": f"sha256:{'a' * 64}",
    }


def _observations():
    resource = _collection_example()["sources"][0]["resources"][0]
    observations = resource["result"]["observations"]
    for observation in observations:
        content_hash = sha256_uri(canonicalize_json(observation["data"]))
        observation["raw_sha256"] = content_hash
        observation["content_sha256"] = content_hash
    return observations


def _manifest():
    target = _collection_example()["target_manifest"]
    return SimpleNamespace(
        id="manifest-row-1",
        tenant_id="tenant-1",
        topology_row_id="topology-1",
        manifest_id=target["manifest_id"],
        manifest_hash=target["manifest_hash"],
        collection_sequence=target["collection_sequence"],
        base_graph_revision=target["base_graph_revision"],
        base_publication_revision=target["base_publication_revision"],
        profile_revision=target["profile_revision"],
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        expected_resources_json=orjson.dumps(target["expected_resources"]),
        creator_subject="user-1",
        playbook_id="synthetic-playbook",
        run_id="synthetic-run-1",
        status=ManifestStatus.ISSUED,
        deleted=False,
    )


async def _open_session(repository, *, upload_id="upload-1"):
    return await start_upload(
        AUTHORITY,
        _start_payload(),
        repository=repository,
        clock=lambda: NOW,
        id_factory=lambda: upload_id,
    )


def _assert_code(error, code):
    assert isinstance(error.value, UploadError)
    assert error.value.code == code


@pytest.mark.asyncio
async def test_chunks_are_accepted_out_of_order_and_same_hash_retry_is_idempotent():
    repository = MemoryRepository(_manifest())
    await _open_session(repository)
    first, second = _observations()

    result_2 = await stage_chunk(
        _owner(), _chunk(second), repository=repository, clock=lambda: NOW
    )
    result_1 = await stage_chunk(
        _owner(), _chunk(first), repository=repository, clock=lambda: NOW
    )
    duplicate = await stage_chunk(
        _owner(), _chunk(second), repository=repository, clock=lambda: NOW
    )

    assert [result_2["observation_id"], result_1["observation_id"]] == [
        second["observation_id"],
        first["observation_id"],
    ]
    assert duplicate["duplicate"] is True
    assert len(repository.staging) == 2


@pytest.mark.asyncio
async def test_same_observation_id_with_different_authoritative_hash_conflicts():
    repository = MemoryRepository(_manifest())
    await _open_session(repository)
    observation = _observations()[0]
    await stage_chunk(
        _owner(), _chunk(observation), repository=repository, clock=lambda: NOW
    )
    changed = deepcopy(observation)
    changed["data"]["hostname"] = "DIFFERENT"
    changed["raw_sha256"] = changed["content_sha256"] = sha256_uri(
        canonicalize_json(changed["data"])
    )

    with pytest.raises(UploadError) as conflict:
        await stage_chunk(
            _owner(), _chunk(changed), repository=repository, clock=lambda: NOW
        )
    _assert_code(conflict, UPLOAD_CONFLICT)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [UploadSessionStatus.COMMITTING, UploadSessionStatus.COMMITTED, UploadSessionStatus.ABORTED]
)
async def test_non_open_session_rejects_chunks(status):
    repository = MemoryRepository(_manifest())
    await _open_session(repository)
    repository.sessions["upload-1"].status = status

    with pytest.raises(UploadError) as invalid:
        await stage_chunk(
            _owner(),
            _chunk(_observations()[0]),
            repository=repository,
            clock=lambda: NOW,
        )
    _assert_code(invalid, UPLOAD_STATE_INVALID)


@pytest.mark.asyncio
async def test_expired_session_is_transitioned_and_rejected():
    repository = MemoryRepository(_manifest())
    await _open_session(repository)

    with pytest.raises(UploadError) as expired:
        await stage_chunk(
            _owner(),
            _chunk(_observations()[0]),
            repository=repository,
            clock=lambda: NOW + timedelta(minutes=6),
        )
    _assert_code(expired, UPLOAD_EXPIRED)
    assert repository.sessions["upload-1"].status == UploadSessionStatus.EXPIRED


@pytest.mark.asyncio
async def test_session_owner_and_tenant_are_isolated():
    repository = MemoryRepository(_manifest())
    await _open_session(repository)
    chunk = _chunk(_observations()[0])
    outsiders = [
        UploadOwner("tenant-2", "user-1"),
        UploadOwner("tenant-1", "user-2"),
    ]

    for outsider in outsiders:
        with pytest.raises(UploadError) as missing:
            await stage_chunk(
                outsider, chunk, repository=repository, clock=lambda: NOW
            )
        _assert_code(missing, UPLOAD_NOT_FOUND)


@pytest.mark.asyncio
async def test_staging_contains_only_redacted_observation_payload():
    repository = MemoryRepository(_manifest())
    await _open_session(repository)
    observation = deepcopy(_observations()[0])
    observation["command_or_api"] = "password test-only-secret"
    await stage_chunk(
        _owner(), _chunk(observation), repository=repository, clock=lambda: NOW
    )

    serialized = next(iter(repository.staging.values())).observation_payload
    assert "test-only-secret" not in serialized
    assert "password" not in serialized.lower()


@pytest.mark.asyncio
async def test_abort_and_bounded_sweep_remove_only_orphan_staging():
    repository = MemoryRepository(_manifest())
    await _open_session(repository, upload_id="aborted")
    await stage_chunk(
        _owner(),
        _chunk(_observations()[0], upload_id="aborted"),
        repository=repository,
        clock=lambda: NOW,
    )
    await abort_upload_session(_owner(), "aborted", repository=repository)
    await _open_session(repository, upload_id="committed")
    await stage_chunk(
        _owner(),
        _chunk(_observations()[1], upload_id="committed"),
        repository=repository,
        clock=lambda: NOW,
    )
    repository.sessions["committed"].status = UploadSessionStatus.COMMITTED

    result = await sweep_upload_sessions(
        repository=repository, clock=lambda: NOW, session_limit=1, staging_limit=1
    )

    assert result.expired_sessions == 0
    assert result.cleaned_staging == 1
    committed = repository.sessions["committed"]
    committed_rows = [
        row
        for row in repository.staging.values()
        if row.upload_session_row_id == committed.id
    ]
    assert committed_rows[0].deleted is False


@pytest.mark.asyncio
async def test_commit_freezes_session_before_transient_snapshot_failure(monkeypatch):
    from services.network_topology import upload as upload_module

    repository = MemoryRepository(_manifest())
    await _open_session(repository)
    for observation in _observations():
        await stage_chunk(
            _owner(), _chunk(observation), repository=repository, clock=lambda: NOW
        )
    repository.sessions["upload-1"].client_expected_input_hash = _authoritative_hash()

    async def fail_commit(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("temporary database failure")

    monkeypatch.setattr(upload_module, "commit_prepared_snapshot", fail_commit)
    with pytest.raises(RuntimeError, match="temporary database"):
        await commit_upload_session(
            _owner(),
            "upload-1",
            expected_payload_hash=_authoritative_hash(),
            repository=repository,
            store=_InlineStore(),
            clock=lambda: NOW,
        )

    assert repository.sessions["upload-1"].status == UploadSessionStatus.COMMITTING
    with pytest.raises(UploadError) as frozen:
        await stage_chunk(
            _owner(),
            _chunk(_observations()[0]),
            repository=repository,
            clock=lambda: NOW,
        )
    _assert_code(frozen, UPLOAD_STATE_INVALID)


@pytest.mark.asyncio
async def test_incomplete_commit_aborts_before_shared_snapshot_commit(monkeypatch):
    from services.network_topology import upload as upload_module

    repository = MemoryRepository(_manifest())
    await _open_session(repository)
    await stage_chunk(
        _owner(),
        _chunk(_observations()[0]),
        repository=repository,
        clock=lambda: NOW,
    )
    repository.sessions["upload-1"].client_expected_input_hash = _authoritative_hash()

    async def parser_boundary(*args, **kwargs):
        del args, kwargs
        raise AssertionError("incomplete snapshot reached parser boundary")

    monkeypatch.setattr(upload_module, "commit_prepared_snapshot", parser_boundary)
    with pytest.raises(UploadError) as invalid:
        await commit_upload_session(
            _owner(),
            "upload-1",
            expected_payload_hash=_authoritative_hash(),
            repository=repository,
            store=_InlineStore(),
            clock=lambda: NOW,
        )

    _assert_code(invalid, upload_module.SCHEMA_INVALID)
    assert repository.sessions["upload-1"].status == UploadSessionStatus.ABORTED


def _chunk(observation, *, upload_id="upload-1"):
    return {
        "upload_session_id": upload_id,
        "resource_id": "synthetic-router-1",
        "observation": deepcopy(observation),
    }


def _owner():
    return UploadOwner(AUTHORITY.tenant_id, AUTHORITY.subject)


def _authoritative_hash():
    from services.network_topology.payload_store import prepare_stored_envelope

    envelope = _collection_example()
    envelope["sources"][0]["resources"][0]["result"][
        "observations"
    ] = _observations()
    return prepare_stored_envelope(envelope).payload_hash


class _InlineStore:
    async def prepare(self, tenant_id, envelope, *, now=None):
        del now
        from services.network_topology.payload_store import (
            PreparedPayload,
            prepare_stored_envelope,
        )

        prepared = prepare_stored_envelope(envelope)
        return PreparedPayload(
            tenant_id=tenant_id,
            payload_hash=prepared.payload_hash,
            size_bytes=len(prepared.canonical_bytes),
            inline_payload=prepared.canonical_bytes.decode(),
        )


def test_upload_blueprint_exposes_start_chunk_and_commit():
    from services.network_topology.upload_views import upload_bp

    routes = {(route.uri, route.methods) for route in upload_bp._future_routes}
    assert routes == {
        ("/start", frozenset({"POST"})),
        ("/chunk", frozenset({"POST"})),
        ("/commit", frozenset({"POST"})),
    }


@pytest.mark.asyncio
async def test_commit_route_returns_202_with_v2_accepted_contract(monkeypatch):
    from services.network_topology import upload as upload_module
    from services.network_topology import upload_views
    from services.network_topology.snapshot import SyncAccepted

    async def committed(*args, **kwargs):
        del args, kwargs
        return SyncAccepted("analysis-1", "snapshot-1")

    monkeypatch.setattr(upload_module, "commit_upload_session", committed)
    body = orjson.dumps(
        {
            "upload_session_id": "upload-1",
            "client_expected_input_hash": f"sha256:{'a' * 64}",
        }
    )
    request = SimpleNamespace(
        query_string="",
        headers={"content-type": "application/json"},
        body=body,
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "sub": "user-1"}),
    )

    response = await inspect.unwrap(upload_views.commit_upload)(request)

    assert response.status == 202
    assert orjson.loads(response.body)["data"]["sync_id"] == "analysis-1"
