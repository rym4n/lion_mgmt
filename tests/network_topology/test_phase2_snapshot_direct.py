from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import subprocess
import sys
from types import SimpleNamespace

import orjson
import pytest

from models._network_topology_v2_base import ManifestStatus, PayloadObjectStatus
from services.network_topology import contracts
from services.network_topology.canonicalization import canonicalize_json, sha256_uri
from services.network_topology.payload_store import (
    PreparedPayload,
    prepare_stored_envelope,
)
from services.network_topology.snapshot import (
    SnapshotError,
    commit_prepared_snapshot,
    ingest_direct_snapshot,
    sync_topology_snapshot,
    sanitize_config_sync_audit,
    validate_direct_snapshot,
)


NOW = datetime(2026, 7, 30, 0, 2, tzinfo=timezone.utc)
SECRET = "customer-super-secret"


def _envelope(*, snapshot_id="snapshot-1"):
    value = deepcopy(
        contracts.load_schema("aioe.network-topology.collection/v2")["examples"][0]
    )
    value["snapshot_id"] = snapshot_id
    value["target_manifest"]["issued_at"] = "2026-07-30T00:00:00Z"
    value["target_manifest"]["expires_at"] = "2026-07-30T00:05:00Z"
    value["collection_started_at"] = "2026-07-30T00:01:00Z"
    value["collection_ended_at"] = "2026-07-30T00:02:00Z"
    for resource in value["sources"][0]["resources"]:
        for observation in resource["result"]["observations"]:
            observation["observed_at"] = "2026-07-30T00:01:30Z"
            raw = canonicalize_json(observation["data"])
            observation["raw_sha256"] = sha256_uri(raw)
            observation["content_sha256"] = sha256_uri(raw)
    manifest = dict(value["target_manifest"])
    manifest.pop("manifest_hash")
    from services.network_topology.canonicalization import (
        canonicalize_and_hash_manifest,
    )

    canonical = canonicalize_and_hash_manifest(manifest)
    value["target_manifest"] = {
        **canonical.projection["target_manifest"],
        "manifest_hash": canonical.manifest_hash,
    }
    return value


def _secret_envelope():
    value = _envelope()
    value["authoritative_context"]["subnets"] = [
        {
            "record_id": "subnet-1",
            "name": "private subnet",
            "scope_ids": [],
            "attributes": {"password": SECRET, "cidr": "192.0.2.0/24"},
            "provenance": {
                "source_system": "cmdb",
                "source_record_id": "subnet-1",
                "source_revision": "1",
                "as_of": "2026-07-30T00:01:00Z",
                "field_locator": "subnets/1",
            },
        }
    ]
    return value


class _Connection:
    def __init__(self, repo):
        self.repo = repo
        self.pending = deepcopy(repo.state)


class _Repository:
    def __init__(self, envelope, *, fail_commit=False):
        manifest = dict(envelope["target_manifest"])
        expected_resources_json = orjson.dumps(
            manifest.pop("expected_resources")
        ).decode()
        self.state = {
            "manifest": SimpleNamespace(
                id="manifest-row-1",
                tenant_id="tenant-1",
                topology_row_id="topology-1",
                profile_revision_row_id="profile-row-1",
                creator_subject="account-1",
                playbook_id=envelope["collector"]["playbook_id"],
                run_id=envelope["collector"]["run_id"],
                status=ManifestStatus.ISSUED,
                consumed_at=None,
                committed_snapshot_row_id=None,
                committed_snapshot_id=None,
                committed_payload_hash=None,
                committed_analysis_run_row_id=None,
                expected_resources_json=expected_resources_json,
                **manifest,
            ),
            "snapshots": {},
            "runs": {},
            "payloads": {},
            "profile": SimpleNamespace(
                id="profile-row-1",
                revision=manifest["profile_revision"],
                profile_json=orjson.dumps(
                    contracts.load_schema("aioe.network-topology.profile/v1")[
                        "examples"
                    ][0]
                ).decode(),
            ),
        }
        self.fail_commit = fail_commit
        self.transactions = 0

    @asynccontextmanager
    async def transaction(self):
        self.transactions += 1
        connection = _Connection(self)
        yield connection
        if self.fail_commit:
            self.fail_commit = False
            raise RuntimeError("injected DB commit failure")
        self.state = connection.pending

    async def lock_manifest(self, tenant_id, topology_id, manifest_id, connection):
        row = connection.pending["manifest"]
        if (row.tenant_id, row.topology_row_id, row.manifest_id) != (
            tenant_id,
            topology_id,
            manifest_id,
        ):
            return None
        return row

    async def get_manifest(self, tenant_id, topology_id, manifest_id):
        row = self.state["manifest"]
        if (row.tenant_id, row.topology_row_id, row.manifest_id) != (
            tenant_id,
            topology_id,
            manifest_id,
        ):
            return None
        return row

    async def get_profile(self, tenant_id, topology_id, profile_id):
        del tenant_id, topology_id
        profile = self.state["profile"]
        return profile if profile.id == profile_id else None

    async def get_snapshot(self, tenant_id, topology_id, snapshot_id, connection):
        return connection.pending["snapshots"].get(
            (tenant_id, topology_id, snapshot_id)
        )

    async def create_snapshot(self, values, connection):
        row = SimpleNamespace(id="snapshot-row-1", **values)
        key = (values["tenant_id"], values["topology_row_id"], values["snapshot_id"])
        connection.pending["snapshots"][key] = row
        return row

    async def create_analysis_run(self, values, connection):
        row = SimpleNamespace(id="sync-1", **values)
        connection.pending["runs"][row.id] = row
        return row

    async def save_manifest(self, manifest, connection):
        connection.pending["manifest"] = manifest


def test_orm_manifest_save_compiles_foreign_key_updates():
    # Model/connection initialization is process-global in Tortoise. Keep the
    # real SQL compiler isolated from the suite's in-memory repository tests.
    probe = """
import asyncio
from tortoise import Tortoise, connections
from models.network_topology_v2 import TopologyCollectionManifest
from services.network_topology.snapshot import OrmSnapshotRepository

async def main():
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
    try:
        class Manifest:
            async def save(self, *, using_db, update_fields):
                executor = using_db.executor_class(
                    model=TopologyCollectionManifest, db=using_db
                )
                sql = executor.get_update_sql(update_fields, {})
                assert '"committed_snapshot_row_id"=' in sql
                assert '"committed_analysis_run_row_id"=' in sql

        await OrmSnapshotRepository().save_manifest(Manifest(), connections.get("default"))
    finally:
        await Tortoise.close_connections()

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


class _PayloadStore:
    def __init__(
        self, prepared=None, *, repository=None, fail_prepare_at=None, fail_live=False
    ):
        self.repository = repository
        self.prepared = prepared or PreparedPayload(
            tenant_id="tenant-1",
            payload_hash="sha256:" + "a" * 64,
            size_bytes=100,
            inline_payload='{"safe":true}',
        )
        self.fail_prepare_at = fail_prepare_at
        self.fail_live = fail_live
        self.prepare_calls = 0
        self.live_calls = 0
        self.deleted = []

    async def prepare(self, tenant_id, raw_envelope, *, now=None):
        self.prepare_calls += 1
        point, self.fail_prepare_at = self.fail_prepare_at, None
        object_id = f"object-{self.prepare_calls}"
        if self.repository is not None:
            self.repository.state["payloads"][object_id] = SimpleNamespace(
                id=object_id,
                status=PayloadObjectStatus.STAGING,
                reference_count=0,
                deleted=False,
            )
        if point == "put":
            raise RuntimeError(f"injected {point} failure")
        redacted = prepare_stored_envelope(raw_envelope)
        if point == "READY":
            raise RuntimeError(f"injected {point} failure")
        if self.repository is not None:
            self.repository.state["payloads"][
                object_id
            ].status = PayloadObjectStatus.READY
        return PreparedPayload(
            tenant_id=tenant_id,
            payload_hash=redacted.payload_hash,
            size_bytes=len(redacted.canonical_bytes),
            inline_payload=(
                redacted.canonical_bytes.decode() if self.repository is None else None
            ),
            payload_object_id=(None if self.repository is None else object_id),
        )

    async def activate(self, tenant_id, prepared, *, connection, now=None):
        self.live_calls += 1
        if self.fail_live:
            self.fail_live = False
            raise RuntimeError("injected LIVE failure")
        if self.repository is not None:
            row = connection.pending["payloads"][prepared.payload_object_id]
            row.status = PayloadObjectStatus.LIVE
            row.reference_count += 1
        return None

    async def sweep_orphans(self, tenant_id, *, now=None):
        del tenant_id, now
        for object_id, row in list(self.repository.state["payloads"].items()):
            if row.status == PayloadObjectStatus.LIVE or row.reference_count:
                continue
            row.deleted = True
            self.deleted.append(object_id)


def test_validation_finishes_before_payload_preparation_or_transaction():
    envelope = _envelope()
    envelope["completeness"]["collected_resource_ids"] = []
    repo = _Repository(_envelope())
    store = _PayloadStore(repository=repo)

    with pytest.raises(SnapshotError) as error:
        validate_direct_snapshot("topology-1", envelope, body_size=100, now=NOW)
    assert error.value.status == 422
    assert store.prepare_calls == 0
    assert repo.transactions == 0


def test_admission_stage_order_is_frozen(monkeypatch):
    from services.network_topology import snapshot as snapshot_module

    envelope = _envelope()
    prepared = prepare_stored_envelope(envelope)
    calls = []

    def record(name, result=None):
        def stage(*args, **kwargs):
            del args, kwargs
            calls.append(name)
            return result

        return stage

    monkeypatch.setattr(snapshot_module, "_validate_size", record("size"))
    monkeypatch.setattr(snapshot_module, "_validate_schema", record("schema", envelope))
    monkeypatch.setattr(
        snapshot_module, "_validate_manifest_projection", record("manifest")
    )
    monkeypatch.setattr(
        snapshot_module, "_validate_completeness", record("completeness")
    )
    monkeypatch.setattr(
        snapshot_module,
        "_validate_collection_window",
        record("window", (NOW, NOW)),
    )
    monkeypatch.setattr(snapshot_module, "_prepare_redacted", record("hash", prepared))

    validate_direct_snapshot("topology-1", envelope, body_size=100, now=NOW)
    assert calls == ["size", "schema", "manifest", "completeness", "window", "hash"]


def test_raw_secret_is_removed_before_any_persistable_value_is_built():
    envelope = _secret_envelope()
    validated = validate_direct_snapshot(
        "topology-1", envelope, body_size=len(orjson.dumps(envelope)), now=NOW
    )

    serialized = orjson.dumps(validated.stored_envelope)
    assert SECRET.encode() not in serialized
    assert b"password" not in serialized.lower()
    assert SECRET not in repr(validated)


@pytest.mark.asyncio
async def test_exact_retry_returns_original_sync():
    envelope = _envelope()
    repo = _Repository(envelope)
    store = _PayloadStore()
    first = await ingest_direct_snapshot(
        "tenant-1",
        "topology-1",
        "account-1",
        envelope,
        body_size=len(orjson.dumps(envelope)),
        now=NOW,
        repository=repo,
        store=store,
    )
    retry = await ingest_direct_snapshot(
        "tenant-1",
        "topology-1",
        "account-1",
        envelope,
        body_size=len(orjson.dumps(envelope)),
        now=NOW,
        repository=repo,
        store=store,
    )
    assert retry == first
    assert retry.sync_id == "sync-1"


@pytest.mark.asyncio
async def test_snapshot_or_manifest_conflicts_return_409():
    envelope = _envelope()
    repo = _Repository(envelope)
    store = _PayloadStore()
    await ingest_direct_snapshot(
        "tenant-1",
        "topology-1",
        "account-1",
        envelope,
        body_size=len(orjson.dumps(envelope)),
        now=NOW,
        repository=repo,
        store=store,
    )

    changed = _envelope(snapshot_id="snapshot-1")
    changed["authoritative_context"]["scopes"] = []
    changed["collector"]["collector_version"] = "1.0.1"
    with pytest.raises(SnapshotError, match="SNAPSHOT_CONFLICT") as snapshot_error:
        await ingest_direct_snapshot(
            "tenant-1",
            "topology-1",
            "account-1",
            changed,
            body_size=len(orjson.dumps(changed)),
            now=NOW,
            repository=repo,
            store=store,
        )
    assert snapshot_error.value.status == 409

    rebound = _envelope(snapshot_id="snapshot-2")
    with pytest.raises(SnapshotError, match="MANIFEST_CONFLICT") as manifest_error:
        await ingest_direct_snapshot(
            "tenant-1",
            "topology-1",
            "account-1",
            rebound,
            body_size=len(orjson.dumps(rebound)),
            now=NOW,
            repository=repo,
            store=store,
        )
    assert manifest_error.value.status == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["put", "READY", "DB commit", "LIVE"])
async def test_each_fault_point_retries_to_one_committed_binding(fault):
    envelope = _envelope()
    repo = _Repository(envelope, fail_commit=fault == "DB commit")
    store = _PayloadStore(
        repository=repo,
        fail_prepare_at=fault if fault in {"put", "READY"} else None,
        fail_live=fault == "LIVE",
    )
    kwargs = dict(
        body_size=len(orjson.dumps(envelope)), now=NOW, repository=repo, store=store
    )
    with pytest.raises(RuntimeError, match="injected"):
        await ingest_direct_snapshot(
            "tenant-1", "topology-1", "account-1", envelope, **kwargs
        )
    assert repo.state["manifest"].status == ManifestStatus.ISSUED
    assert repo.state["snapshots"] == repo.state["runs"] == {}
    assert not any(
        row.status == PayloadObjectStatus.LIVE
        for row in repo.state["payloads"].values()
    )
    result = await ingest_direct_snapshot(
        "tenant-1", "topology-1", "account-1", envelope, **kwargs
    )
    assert result.sync_id == "sync-1"
    assert repo.state["manifest"].status == ManifestStatus.COMMITTED
    assert len(repo.state["snapshots"]) == len(repo.state["runs"]) == 1
    live_ids = {
        object_id
        for object_id, row in repo.state["payloads"].items()
        if row.status == PayloadObjectStatus.LIVE and row.reference_count == 1
    }
    assert len(live_ids) == 1
    await store.sweep_orphans("tenant-1", now=NOW)
    assert not (live_ids & set(store.deleted))


@pytest.mark.asyncio
async def test_http_202_has_safe_error_and_audit_bodies(monkeypatch, caplog):
    import settings

    monkeypatch.setattr(settings, "TOPOLOGY_V2_INGRESS_ENABLED", True)
    envelope = _secret_envelope()
    request = SimpleNamespace(
        json={"id": "topology-1", "snapshot": envelope},
        body=orjson.dumps({"id": "topology-1", "snapshot": envelope}),
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "sub": "account-1"}),
    )
    repo = _Repository(envelope)
    response = await sync_topology_snapshot(
        request, repository=repo, store=_PayloadStore(repository=repo), now=NOW
    )
    assert response.status == 202
    assert SECRET.encode() not in response.body
    assert SECRET.encode() not in request.body
    assert SECRET not in caplog.text
    assert SECRET not in repr(repo.state)


def test_legacy_audit_summary_contains_no_config_body():
    request = SimpleNamespace(
        json={"id": "topology-1", "config": f"password {SECRET}"},
        body=f"password {SECRET}".encode(),
    )
    sanitize_config_sync_audit(request)
    summary = orjson.loads(request.body)
    assert summary == {"id": "topology-1", "legacy": True, "deprecated": True}
    assert SECRET.encode() not in request.body


@pytest.mark.asyncio
async def test_oversize_http_request_is_rejected_before_json_parse(monkeypatch):
    import settings

    class Request:
        body = b"x" * 11
        ctx = SimpleNamespace(session={})

        @property
        def json(self):
            raise AssertionError("oversize body must not be parsed")

    monkeypatch.setattr(settings, "TOPOLOGY_V2_DIRECT_MAX_BYTES", 10, raising=False)
    response = await sync_topology_snapshot(Request(), now=NOW)
    assert response.status == 413
    assert b"xxxxx" not in response.body


@pytest.mark.asyncio
async def test_http_infrastructure_error_is_safe_503(monkeypatch):
    import settings

    envelope = _secret_envelope()
    request = SimpleNamespace(
        json={"id": "topology-1", "snapshot": envelope},
        body=orjson.dumps({"id": "topology-1", "snapshot": envelope}),
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "sub": "account-1"}),
    )
    repo = _Repository(envelope)
    store = _PayloadStore(repository=repo, fail_prepare_at="put")
    monkeypatch.setattr(settings, "TOPOLOGY_V2_INGRESS_ENABLED", True)

    response = await sync_topology_snapshot(
        request, repository=repo, store=store, now=NOW
    )
    assert response.status == 503
    assert SECRET.encode() not in response.body
    assert SECRET.encode() not in request.body


@pytest.mark.asyncio
async def test_commit_public_signature_supports_chunk_upload_reuse():
    envelope = _envelope()
    validated = validate_direct_snapshot(
        "topology-1", envelope, body_size=len(orjson.dumps(envelope)), now=NOW
    )
    prepared = PreparedPayload(
        tenant_id="tenant-1",
        payload_hash=validated.payload_hash,
        size_bytes=len(validated.prepared_envelope.canonical_bytes),
        inline_payload=validated.prepared_envelope.canonical_bytes.decode(),
    )
    result = await commit_prepared_snapshot(
        "tenant-1",
        "topology-1",
        "account-1",
        validated,
        prepared,
        repository=_Repository(envelope),
        store=_PayloadStore(prepared),
        now=NOW,
    )
    assert result.snapshot_id == "snapshot-1"
