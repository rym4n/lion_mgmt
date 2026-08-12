"""Recoverable commit workflow for chunked topology snapshot uploads."""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import orjson

from models._network_topology_v2_base import ManifestStatus, UploadSessionStatus
from services.network_topology.canonicalization import (
    CanonicalizationError,
    canonicalize_and_hash_snapshot,
    canonicalize_json,
    canonicalize_observation_data,
    sha256_uri,
)
from services.network_topology.payload_store import (
    PayloadStore,
    PreparedEnvelope,
    PreparedPayload,
)
from services.network_topology.snapshot import (
    COLLECTION_WINDOW_INVALID,
    COMPLETENESS_INVALID,
    INGRESS_DISABLED,
    INVALID_REQUEST,
    MANIFEST_CONFLICT,
    MANIFEST_INVALID,
    PAYLOAD_HASH_INVALID,
    SCHEMA_INVALID as SNAPSHOT_SCHEMA_INVALID,
    SNAPSHOT_CONFLICT,
    SYNC_UNAVAILABLE,
    OrmSnapshotRepository,
    SnapshotError,
    SyncAccepted,
    ValidatedSnapshot,
    validate_direct_snapshot,
)
from services.network_topology.upload import (
    MANIFEST_CONSUMED,
    MANIFEST_MISMATCH,
    SCHEMA_INVALID,
    UPLOAD_CONFLICT,
    UPLOAD_EXPIRED,
    UPLOAD_NOT_FOUND,
    UPLOAD_STATE_INVALID,
    OrmUploadRepository,
    UploadError,
)


@dataclass(frozen=True, slots=True)
class FrozenUpload:
    row_id: str
    tenant_id: str
    topology_id: str
    snapshot_id: str
    envelope_json: str
    expected_count: int
    transport_hash: str


async def commit_upload_session_impl(
    owner,
    upload_session_id,
    *,
    expected_payload_hash,
    repository,
    snapshot_repository,
    store,
    clock,
    id_factory,
    commit_snapshot,
):
    from services.network_topology.upload import _require_enabled, _validate_owner

    _require_enabled()
    _validate_owner(owner)
    now = _aware_now(clock())
    upload_repo = repository or OrmUploadRepository()
    try:
        frozen = await _claim_upload(
            upload_repo, owner, upload_session_id, expected_payload_hash, now
        )
        if isinstance(frozen, SyncAccepted):
            return frozen
        _require_expected_hash(frozen.transport_hash, expected_payload_hash)
        return await _commit_frozen_upload(
            frozen,
            owner,
            upload_session_id,
            upload_repo,
            snapshot_repository,
            store,
            now,
            id_factory,
            commit_snapshot,
        )
    except SnapshotError as error:
        mapped = _mapped_snapshot_error(error)
        if mapped is None:
            raise
        await _abort_committing(upload_repo, owner, upload_session_id)
        raise mapped from None
    except UploadError:
        await _abort_committing(upload_repo, owner, upload_session_id)
        raise


async def _commit_frozen_upload(
    frozen, owner, upload_id, uploads, snapshot_repo, store, now, id_factory, commit
):
    envelope = await _assemble_stored_envelope(uploads, frozen)
    validated = _validate_stored_snapshot(frozen, envelope, now)
    _require_transport_hash(frozen.transport_hash, validated.payload_hash)
    payload_store = store or PayloadStore()
    prepared = await _prepare_payload(
        payload_store, frozen.tenant_id, validated.prepared_envelope, now
    )
    atomic_repo = SessionCommitRepository(
        snapshot_repo or OrmSnapshotRepository(),
        uploads,
        owner,
        upload_id,
        validated,
        prepared,
        now,
    )
    return await commit(
        frozen.tenant_id,
        frozen.topology_id,
        owner.subject,
        validated,
        prepared,
        repository=atomic_repo,
        store=payload_store,
        now=now,
        id_factory=id_factory,
    )


async def _claim_upload(repo, owner, upload_id, expected_hash, now):
    expired = False
    async with repo.transaction() as connection:
        session = await repo.lock_session(owner, upload_id, connection)
        if session is None:
            raise UploadError(UPLOAD_NOT_FOUND)
        if session.status == UploadSessionStatus.COMMITTED:
            _require_expected_hash(
                session.client_expected_input_hash, expected_hash
            )
            return _committed_result(session)
        if session.status not in (
            UploadSessionStatus.OPEN,
            UploadSessionStatus.COMMITTING,
        ):
            raise UploadError(UPLOAD_STATE_INVALID)
        if session.status == UploadSessionStatus.OPEN and session.expires_at <= now:
            session.status, expired = UploadSessionStatus.EXPIRED, True
        elif session.status == UploadSessionStatus.OPEN:
            session.status = UploadSessionStatus.COMMITTING
        await repo.save_session(session, connection)
        frozen = _freeze_session(session)
    if expired:
        raise UploadError(UPLOAD_EXPIRED)
    return frozen


def _freeze_session(session) -> FrozenUpload:
    return FrozenUpload(
        str(session.id),
        str(session.tenant_id),
        str(session.topology_row_id),
        session.snapshot_id,
        session.envelope_json,
        session.expected_observation_count,
        session.client_expected_input_hash,
    )


def _committed_result(session) -> SyncAccepted:
    sync_id = getattr(session, "committed_analysis_run_row_id", None)
    if not sync_id:
        raise UploadError(UPLOAD_STATE_INVALID)
    return SyncAccepted(str(sync_id), session.snapshot_id)


def _require_expected_hash(frozen_hash, supplied_hash) -> None:
    if supplied_hash is not None and supplied_hash != frozen_hash:
        raise UploadError(UPLOAD_CONFLICT)


async def _assemble_stored_envelope(repo, frozen) -> dict[str, Any]:
    try:
        envelope = orjson.loads(frozen.envelope_json)
        rows = await repo.list_staging(frozen.tenant_id, frozen.row_id)
        if len(rows) != frozen.expected_count:
            raise UploadError(SCHEMA_INVALID)
        resources = {
            item["resource_id"]: item
            for source in envelope["sources"]
            for item in source["resources"]
        }
        grouped = {resource_id: [] for resource_id in resources}
        for row in rows:
            grouped[row.resource_id].append(_staged_observation(row))
        _attach_observations(resources, grouped)
        return envelope
    except (KeyError, TypeError, orjson.JSONDecodeError, CanonicalizationError):
        raise UploadError(SCHEMA_INVALID) from None


def _staged_observation(row) -> dict[str, Any]:
    payload = orjson.loads(row.observation_payload)
    canonical = canonicalize_and_hash_snapshot(payload)
    if (
        set(payload) != {"observation"}
        or canonical.payload_hash != row.content_hash
        or len(canonical.canonical_bytes) != row.size_bytes
        or payload["observation"]["observation_id"] != row.observation_id
    ):
        raise UploadError(SCHEMA_INVALID)
    return payload["observation"]


def _attach_observations(resources, grouped) -> None:
    for resource_id, resource in resources.items():
        expected = resource["result"].pop("expected_observation_count")
        observations = grouped[resource_id]
        if len(observations) != expected:
            raise UploadError(SCHEMA_INVALID)
        resource["result"]["observations"] = observations


def _validate_stored_snapshot(frozen, envelope, now) -> ValidatedSnapshot:
    validation_copy = deepcopy(envelope)
    _refresh_validation_hashes(validation_copy)
    validated = validate_direct_snapshot(
        frozen.topology_id,
        validation_copy,
        body_size=len(canonicalize_json(validation_copy)),
        now=now,
    )
    if validated.snapshot_id != frozen.snapshot_id:
        raise UploadError(SCHEMA_INVALID)
    canonical = canonicalize_and_hash_snapshot(envelope)
    prepared = PreparedEnvelope(
        canonical.normalized, canonical.canonical_bytes, canonical.payload_hash
    )
    return ValidatedSnapshot(
        frozen.topology_id,
        frozen.snapshot_id,
        canonical.normalized,
        prepared,
        validated.started_at,
        validated.ended_at,
    )


def _refresh_validation_hashes(envelope) -> None:
    for source in envelope["sources"]:
        for resource in source["resources"]:
            for observation in resource["result"]["observations"]:
                data = observation["data"]
                content_type = observation["content_type"]
                raw = (
                    canonicalize_json(data)
                    if content_type == "application/json"
                    else data.encode()
                )
                observation["raw_sha256"] = sha256_uri(raw)
                observation["content_sha256"] = sha256_uri(
                    canonicalize_observation_data(content_type, data)
                )


def _require_transport_hash(expected, authoritative) -> None:
    if expected != authoritative:
        raise UploadError(UPLOAD_CONFLICT)


async def _prepare_payload(store, tenant_id, envelope, now) -> PreparedPayload:
    threshold = getattr(store, "inline_threshold", None)
    if isinstance(threshold, int):
        if len(envelope.canonical_bytes) <= threshold:
            return _inline_payload(tenant_id, envelope)
        return await store._prepare_object(tenant_id, envelope, now)
    prepared = await store.prepare(tenant_id, envelope.normalized, now=now)
    if prepared.payload_hash != envelope.payload_hash:
        raise UploadError(SCHEMA_INVALID)
    return prepared


def _inline_payload(tenant_id, envelope) -> PreparedPayload:
    return PreparedPayload(
        tenant_id=tenant_id,
        payload_hash=envelope.payload_hash,
        size_bytes=len(envelope.canonical_bytes),
        inline_payload=envelope.canonical_bytes.decode(),
    )


class SessionCommitRepository:
    def __init__(self, delegate, uploads, owner, upload_id, validated, prepared, now):
        self.delegate = delegate
        self.uploads = uploads
        self.owner = owner
        self.upload_id = upload_id
        self.validated = validated
        self.prepared = prepared
        self.now = now
        self.manifest = None

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    @asynccontextmanager
    async def transaction(self):
        async with self.delegate.transaction() as connection:
            session = await self.uploads.lock_session(
                self.owner, self.upload_id, connection
            )
            if session is None or session.status != UploadSessionStatus.COMMITTING:
                raise UploadError(UPLOAD_STATE_INVALID)
            yield connection
            await self._commit_session(session, connection)

    async def lock_manifest(self, *args):
        self.manifest = await self.delegate.lock_manifest(*args)
        return self.manifest

    async def _commit_session(self, session, connection) -> None:
        manifest = self.manifest
        valid = (
            manifest is not None
            and manifest.status == ManifestStatus.COMMITTED
            and manifest.committed_snapshot_id == self.validated.snapshot_id
            and manifest.committed_payload_hash == self.prepared.payload_hash
            and manifest.committed_analysis_run_row_id
        )
        if not valid:
            raise UploadError(MANIFEST_MISMATCH)
        session.status = UploadSessionStatus.COMMITTED
        session.authoritative_payload_hash = self.prepared.payload_hash
        session.committed_snapshot_row_id = manifest.committed_snapshot_row_id
        session.committed_analysis_run_row_id = manifest.committed_analysis_run_row_id
        session.committed_at = self.now
        await self.uploads.save_session(session, connection)


async def _abort_committing(repo, owner, upload_id) -> None:
    async with repo.transaction() as connection:
        session = await repo.lock_session(owner, upload_id, connection)
        if session is None or session.status != UploadSessionStatus.COMMITTING:
            return
        session.status = UploadSessionStatus.ABORTED
        await repo.save_session(session, connection)


def _mapped_snapshot_error(error) -> UploadError | None:
    if error.code == SYNC_UNAVAILABLE:
        return None
    if error.code == INGRESS_DISABLED:
        from services.network_topology.upload import UPLOAD_DISABLED

        return UploadError(UPLOAD_DISABLED)
    if error.code in {MANIFEST_INVALID, MANIFEST_CONFLICT}:
        return UploadError(MANIFEST_CONSUMED)
    if error.code == SNAPSHOT_CONFLICT:
        return UploadError(UPLOAD_CONFLICT)
    deterministic = {
        INVALID_REQUEST,
        SNAPSHOT_SCHEMA_INVALID,
        COMPLETENESS_INVALID,
        COLLECTION_WINDOW_INVALID,
        PAYLOAD_HASH_INVALID,
    }
    return UploadError(SCHEMA_INVALID) if error.code in deterministic else None


def _aware_now(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Upload timestamps must be timezone-aware")
    return value.astimezone(UTC)
