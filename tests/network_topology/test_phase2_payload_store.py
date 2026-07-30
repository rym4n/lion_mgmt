from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from models._network_topology_v2_base import PayloadObjectStatus
from services.network_topology import payload_store
from services.network_topology.canonicalization import (
    CanonicalizationError,
    _canonicalize_xml_engine,
    sha256_uri,
)
from services.network_topology.payload_store import PayloadStore, prepare_stored_envelope


NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)
SECRET = "customer-super-secret"


def _snapshot(secret: str = SECRET) -> dict:
    raw = f"hostname edge-1\npassword {secret}\ninterface eth0\n"
    raw_bytes = raw.encode()
    return {
        "schema_version": "aioe.network-topology.collection/v2",
        "snapshot_id": "snapshot-1",
        "observations": [
            {
                "observation_id": "observation-1",
                "content_type": "text/plain",
                "raw_sha256": sha256_uri(raw_bytes),
                "content_sha256": sha256_uri(raw_bytes),
                "data": raw,
            }
        ],
    }


class _Codec:
    encryption_version = "test-envelope/v1"
    key_version = "test-key/v1"

    def encrypt(self, value: bytes) -> bytes:
        return b"cipher:" + value[::-1]

    def decrypt(self, value: bytes) -> bytes:
        assert value.startswith(b"cipher:")
        return value.removeprefix(b"cipher:")[::-1]


class _Backend:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    async def put_staging(self, tenant_id, object_key, value, created_at):
        assert tenant_id in object_key
        assert object_key not in self.objects
        self.objects[object_key] = SimpleNamespace(value=value, created_at=created_at)

    async def read(self, object_key):
        return self.objects[object_key].value

    async def delete(self, object_key):
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)

    async def list_staging(self, tenant_id):
        return [
            SimpleNamespace(object_key=key, created_at=item.created_at)
            for key, item in self.objects.items()
            if tenant_id in key
        ]


class _Repository:
    def __init__(self, events=None):
        self.rows = {}
        self.references = set()
        self._next_id = 1
        self.events = events

    async def create_staging(self, **values):
        if self.events is not None:
            self.events.append("db-staging")
        row = SimpleNamespace(id=str(self._next_id), deleted=False, **values)
        self._next_id += 1
        self.rows[row.id] = row
        return row

    async def mark_ready(self, row, *, verified_at, retention_until):
        if self.events is not None:
            self.events.append("db-ready")
        row.status = PayloadObjectStatus.READY
        row.verified_at = verified_at
        row.retention_until = retention_until
        return row

    async def lock(self, tenant_id, object_id, connection):
        row = self.rows.get(object_id)
        if row is None or row.tenant_id != tenant_id or row.deleted:
            return None
        return connection.copy_for_update(row)

    async def save(self, row, connection):
        connection.pending[row.id] = row

    async def expired_orphans(self, tenant_id, before):
        return [
            row
            for row in self.rows.values()
            if row.tenant_id == tenant_id
            and row.status
            in {
                PayloadObjectStatus.STAGING,
                PayloadObjectStatus.READY,
                PayloadObjectStatus.DELETING,
            }
            and row.retention_until <= before
            and not row.deleted
        ]

    async def has_references(self, tenant_id, object_id, connection=None):
        return (tenant_id, object_id) in self.references

    async def mark_deleting_if_unreferenced(self, tenant_id, object_id):
        row = self.rows.get(object_id)
        if row is None or await self.has_references(tenant_id, object_id):
            return None
        if row.status == PayloadObjectStatus.DELETING:
            return row
        if row.status not in {
            PayloadObjectStatus.STAGING,
            PayloadObjectStatus.READY,
        } or row.reference_count:
            return None
        row.status = PayloadObjectStatus.DELETING
        return row

    async def soft_delete(self, row):
        row.deleted = True

    async def has_object_key(self, tenant_id, object_key):
        return any(
            row.tenant_id == tenant_id
            and row.object_key == object_key
            and not row.deleted
            for row in self.rows.values()
        )


class _Connection:
    def __init__(self, repository):
        self.repository = repository
        self.pending = {}

    def copy_for_update(self, row):
        return deepcopy(row)

    def commit(self):
        self.repository.rows.update(self.pending)


@asynccontextmanager
async def _transaction(repository, *, rollback=False):
    connection = _Connection(repository)
    try:
        yield connection
        if rollback:
            raise RuntimeError("business transaction failed")
    except RuntimeError:
        raise
    else:
        connection.commit()


def _store(*, inline_threshold=1):
    backend = _Backend()
    repository = _Repository()
    store = PayloadStore(
        backend=backend,
        repository=repository,
        codec=_Codec(),
        inline_threshold=inline_threshold,
        ready_ttl=timedelta(minutes=5),
    )
    return store, backend, repository


def test_raw_body_is_validated_then_redacted_before_hash_or_storage():
    prepared = prepare_stored_envelope(_snapshot())
    serialized = prepared.canonical_bytes.decode()

    assert SECRET not in serialized
    assert "password" not in serialized.lower()
    assert prepared.payload_hash == sha256_uri(prepared.canonical_bytes)


def test_multiline_xml_credentials_are_removed_as_one_sensitive_element():
    raw = "<config><password>\ncustomer-super-secret\n</password><host>edge-1</host></config>"
    snapshot = _snapshot()
    snapshot["observations"][0].update(
        content_type="application/xml",
        raw_sha256=sha256_uri(raw.encode()),
        content_sha256=sha256_uri(_canonicalize_xml_engine(raw, 2)),
        data=raw,
    )

    serialized = prepare_stored_envelope(snapshot).canonical_bytes.decode()

    assert SECRET not in serialized
    assert "password" not in serialized.lower()
    assert "edge-1" in serialized


def test_partial_observation_hash_contract_fails_closed():
    snapshot = _snapshot()
    del snapshot["observations"][0]["content_sha256"]

    with pytest.raises(CanonicalizationError, match="hash fields"):
        prepare_stored_envelope(snapshot)


@pytest.mark.asyncio
async def test_not_ready_payload_cannot_be_activated():
    store, _backend, repository = _store()
    prepared = await store.prepare("tenant-1", _snapshot(), now=NOW)
    repository.rows[prepared.payload_object_id].status = PayloadObjectStatus.STAGING

    async with _transaction(repository) as connection:
        with pytest.raises(ValueError, match="READY"):
            await store.activate(
                "tenant-1", prepared, connection=connection, now=NOW
            )


@pytest.mark.asyncio
async def test_business_transaction_rollback_leaves_collectable_ready_orphan():
    store, _backend, repository = _store()
    prepared = await store.prepare("tenant-1", _snapshot(), now=NOW)

    with pytest.raises(RuntimeError, match="business transaction failed"):
        async with _transaction(repository, rollback=True) as connection:
            await store.activate(
                "tenant-1", prepared, connection=connection, now=NOW
            )

    row = repository.rows[prepared.payload_object_id]
    assert row.status == PayloadObjectStatus.READY
    assert row.reference_count == 0
    assert row.retention_until == NOW + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_activation_rejects_a_prepared_payload_from_another_tenant():
    store, _backend, repository = _store()
    prepared = await store.prepare("tenant-1", _snapshot(), now=NOW)

    async with _transaction(repository) as connection:
        with pytest.raises(ValueError, match="tenant"):
            await store.activate(
                "tenant-2", prepared, connection=connection, now=NOW
            )


@pytest.mark.asyncio
async def test_orphan_sweeper_rechecks_references_and_never_deletes_live_data():
    store, backend, repository = _store()
    referenced = await store.prepare("tenant-1", _snapshot("secret-a"), now=NOW)
    orphan = await store.prepare("tenant-1", _snapshot("secret-b"), now=NOW)
    for row in repository.rows.values():
        row.retention_until = NOW - timedelta(seconds=1)
    repository.references.add(("tenant-1", referenced.payload_object_id))

    result = await store.sweep_orphans("tenant-1", now=NOW)

    assert result.deleted_records == 1
    assert repository.rows[referenced.payload_object_id].deleted is False
    assert repository.rows[orphan.payload_object_id].deleted is True
    assert prepared_key(repository, referenced) in backend.objects


@pytest.mark.asyncio
async def test_orphan_sweeper_deletes_only_old_unrecorded_staging_objects():
    store, backend, repository = _store()
    recorded = await store.prepare("tenant-1", _snapshot(), now=NOW)
    orphan_key = "network-topology/staging/tenant-1/unrecorded.payload"
    backend.objects[orphan_key] = SimpleNamespace(
        value=b"unreadable-orphan", created_at=NOW - timedelta(minutes=6)
    )

    result = await store.sweep_orphans("tenant-1", now=NOW)

    assert result.deleted_staging_objects == 1
    assert orphan_key not in backend.objects
    assert prepared_key(repository, recorded) in backend.objects


@pytest.mark.asyncio
async def test_deleting_orphan_is_retried_after_object_backend_failure():
    store, backend, repository = _store()
    prepared = await store.prepare("tenant-1", _snapshot(), now=NOW)
    repository.rows[prepared.payload_object_id].retention_until = NOW - timedelta(seconds=1)
    original_delete = backend.delete
    attempts = 0

    async def fail_once(object_key):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary object backend failure")
        await original_delete(object_key)

    backend.delete = fail_once
    with pytest.raises(RuntimeError, match="temporary object backend failure"):
        await store.sweep_orphans("tenant-1", now=NOW)
    assert repository.rows[prepared.payload_object_id].status == PayloadObjectStatus.DELETING

    result = await store.sweep_orphans("tenant-1", now=NOW)

    assert result.deleted_records == 1
    assert repository.rows[prepared.payload_object_id].deleted is True


@pytest.mark.asyncio
async def test_encrypted_object_key_is_not_used_as_an_orm_equality_filter(monkeypatch):
    rows = [SimpleNamespace(object_key="staging-key")]

    class Query:
        async def all(self):
            return rows

    class Model:
        @staticmethod
        def filter(**filters):
            assert "object_key" not in filters
            return Query()

    monkeypatch.setattr(payload_store, "TopologyPayloadObject", Model)
    repository = payload_store.OrmPayloadRepository()

    assert await repository.has_object_key("tenant-1", "staging-key") is True


@pytest.mark.asyncio
async def test_large_payload_claims_staging_before_readability_verification():
    events = []

    class Backend(_Backend):
        async def put_staging(self, *args):
            events.append("put")
            await super().put_staging(*args)

        async def read(self, object_key):
            events.append("read")
            return await super().read(object_key)

    backend = Backend()
    repository = _Repository(events)
    store = PayloadStore(
        backend=backend,
        repository=repository,
        codec=_Codec(),
        inline_threshold=1,
        ready_ttl=timedelta(minutes=5),
    )

    await store.prepare("tenant-1", _snapshot(), now=NOW)

    assert events == ["db-staging", "put", "read", "db-ready"]


@pytest.mark.asyncio
async def test_direct_and_chunk_commit_use_identical_authoritative_hash():
    direct = prepare_stored_envelope(_snapshot())
    chunk_envelope = deepcopy(_snapshot())
    chunk_envelope["transport_attempt"] = 9
    chunked = prepare_stored_envelope(chunk_envelope)

    assert direct.payload_hash == chunked.payload_hash
    assert direct.canonical_bytes == chunked.canonical_bytes


def prepared_key(repository, prepared):
    return repository.rows[prepared.payload_object_id].object_key
