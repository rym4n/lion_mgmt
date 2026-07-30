"""Chunked topology snapshot upload sessions and staging lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

import orjson
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

import settings
from models._network_topology_v2_base import ManifestStatus, UploadSessionStatus
from models._network_topology_v2_collection import (
    TopologyCollectionManifest,
    TopologyObservationStaging,
    TopologySnapshotUploadSession,
)
from services.network_topology import contracts
from services.network_topology.canonicalization import (
    CanonicalizationError,
    canonicalize_and_hash_manifest,
    canonicalize_json,
)
from services.network_topology.manifest import ManifestAuthority
from services.network_topology.payload_store import prepare_stored_envelope
from services.network_topology.snapshot import commit_prepared_snapshot

UPLOAD_START_SCHEMA = "aioe.network-topology.api/upload-start-request/v1"
UPLOAD_CHUNK_SCHEMA = "aioe.network-topology.api/upload-chunk-request/v1"
UPLOAD_COMMIT_SCHEMA = "aioe.network-topology.api/upload-commit-request/v1"
UPLOAD_START_RESPONSE_SCHEMA = "aioe.network-topology.api/upload-start-response/v1"
UPLOAD_CHUNK_RESPONSE_SCHEMA = "aioe.network-topology.api/upload-chunk-response/v1"
UPLOAD_COMMIT_RESPONSE_SCHEMA = "aioe.network-topology.api/upload-commit-response/v1"
DEFAULT_SESSION_TTL = timedelta(minutes=15)
DEFAULT_CLEANUP_LIMIT = 100

SCHEMA_INVALID = "SCHEMA_INVALID"
MANIFEST_MISMATCH = "MANIFEST_MISMATCH"
MANIFEST_CONSUMED = "MANIFEST_CONSUMED"
UPLOAD_CONFLICT = "UPLOAD_CONFLICT"
UPLOAD_DISABLED = "UPLOAD_DISABLED"
UPLOAD_EXPIRED = "UPLOAD_EXPIRED"
UPLOAD_NOT_FOUND = "UPLOAD_NOT_FOUND"
UPLOAD_STATE_INVALID = "UPLOAD_STATE_INVALID"
UPLOAD_UNAVAILABLE = "UPLOAD_UNAVAILABLE"

_PUBLIC_ERRORS = {
    SCHEMA_INVALID: (400, "上传数据不符合拓扑采集契约"),
    MANIFEST_MISMATCH: (409, "上传数据与 Manifest 不匹配"),
    MANIFEST_CONSUMED: (409, "Manifest 已不可用于上传"),
    UPLOAD_CONFLICT: (409, "分片与已接收 observation 冲突"),
    UPLOAD_DISABLED: (503, "拓扑采集入口未启用"),
    UPLOAD_EXPIRED: (409, "上传会话已过期"),
    UPLOAD_NOT_FOUND: (404, "上传会话不存在"),
    UPLOAD_STATE_INVALID: (409, "上传会话当前状态不接受分片"),
    UPLOAD_UNAVAILABLE: (503, "分片提交服务暂时不可用"),
}


class UploadError(RuntimeError):
    """Stable upload failure that never includes source payload data."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.status, self.public_message = _PUBLIC_ERRORS[code]
        super().__init__(code)


UploadAuthority = ManifestAuthority


@dataclass(frozen=True, slots=True, repr=False)
class UploadOwner:
    tenant_id: str
    subject: str


@dataclass(frozen=True, slots=True)
class UploadSweepResult:
    expired_sessions: int
    cleaned_staging: int


class UploadRepository(Protocol):
    def transaction(self) -> AsyncIterator[Any]: ...

    async def lock_manifest(self, authority, manifest_id, connection): ...

    async def create_session(self, connection, **values): ...

    async def lock_session(self, owner, upload_id, connection): ...

    async def find_staging(
        self, tenant_id, session_row_id, observation_id, connection
    ): ...

    async def create_staging(self, connection, **values): ...

    async def list_staging(self, tenant_id, session_row_id): ...

    async def save_session(self, row, connection): ...

    async def cleanup_sessions(self, before, limit) -> tuple[list[str], int]: ...

    async def cleanup_staging(self, session_row_ids, limit) -> int: ...


class OrmUploadRepository:
    @asynccontextmanager
    async def transaction(self):
        async with in_transaction() as connection:
            yield connection

    async def lock_manifest(self, authority, manifest_id, connection):
        query = TopologyCollectionManifest.filter(
            tenant_id=authority.tenant_id,
            topology_row_id=authority.topology_id,
            manifest_id=manifest_id,
            deleted=False,
        )
        return await query.using_db(connection).select_for_update().first()

    async def create_session(self, connection, **values):
        return await TopologySnapshotUploadSession.create(
            using_db=connection, **values
        )

    async def lock_session(self, owner, upload_id, connection):
        query = TopologySnapshotUploadSession.filter(
            tenant_id=owner.tenant_id,
            upload_id=upload_id,
            submitter_subject=owner.subject,
            deleted=False,
        )
        return await query.using_db(connection).select_for_update().first()

    async def find_staging(
        self, tenant_id, session_row_id, observation_id, connection
    ):
        return await TopologyObservationStaging.filter(
            tenant_id=tenant_id,
            upload_session_row_id=session_row_id,
            observation_id=observation_id,
            deleted=False,
        ).using_db(connection).first()

    async def create_staging(self, connection, **values):
        return await TopologyObservationStaging.create(
            using_db=connection, **values
        )

    async def list_staging(self, tenant_id, session_row_id):
        return await TopologyObservationStaging.filter(
            tenant_id=tenant_id,
            upload_session_row_id=session_row_id,
            deleted=False,
        ).order_by("observation_id")

    async def save_session(self, row, connection):
        fields = (
            "status",
            "authoritative_payload_hash",
            "committed_snapshot_row_id",
            "committed_analysis_run_row_id",
            "committed_at",
            "utime",
        )
        await row.save(using_db=connection, update_fields=fields)

    async def cleanup_sessions(self, before, limit):
        async with in_transaction() as connection:
            rows = await self._cleanup_candidates(before, limit, connection)
            expired = await self._expire_open(rows, connection)
        return [str(row.id) for row in rows], expired

    async def cleanup_staging(self, session_row_ids, limit):
        if not session_row_ids:
            return 0
        rows = await TopologyObservationStaging.filter(
            upload_session_row_id__in=session_row_ids,
            deleted=False,
        ).limit(limit)
        for row in rows:
            row.deleted = True
            await row.save(update_fields=("deleted", "utime"))
        return len(rows)

    async def _cleanup_candidates(self, before, limit, connection):
        query = TopologySnapshotUploadSession.filter(
            Q(status=UploadSessionStatus.OPEN, expires_at__lte=before)
            | Q(
                status__in=(
                    UploadSessionStatus.ABORTED,
                    UploadSessionStatus.EXPIRED,
                )
            ),
            deleted=False,
        )
        return await (
            query.using_db(connection)
            .select_for_update()
            .order_by("expires_at", "id")
            .limit(limit)
        )

    async def _expire_open(self, rows, connection):
        expired = 0
        for row in rows:
            if row.status != UploadSessionStatus.OPEN:
                continue
            row.status = UploadSessionStatus.EXPIRED
            await self.save_session(row, connection)
            expired += 1
        return expired


async def start_upload(
    authority: UploadAuthority,
    payload: object,
    *,
    repository: UploadRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    id_factory: Callable[[], str] = lambda: str(uuid4()),
    session_ttl: timedelta = DEFAULT_SESSION_TTL,
) -> dict[str, Any]:
    """Freeze the observation-free envelope and create one OPEN session."""
    _require_enabled()
    request = _validated_start(authority, payload)
    now = _aware_now(clock())
    prepared = _prepare_envelope(request["snapshot_envelope_without_observations"])
    if session_ttl <= timedelta(0):
        raise ValueError("Upload session TTL must be positive")
    repo = repository or OrmUploadRepository()
    async with repo.transaction() as connection:
        manifest = await repo.lock_manifest(
            authority, request["manifest_id"], connection
        )
        _validate_manifest(manifest, authority, request, now)
        expires_at = min(now + session_ttl, manifest.expires_at)
        row = await repo.create_session(
            connection,
            **_session_values(
                authority, request, prepared, manifest, id_factory(), expires_at
            ),
        )
    return _validated_result(_start_result(row), UPLOAD_START_RESPONSE_SCHEMA)


async def stage_chunk(
    owner: UploadOwner,
    payload: object,
    *,
    repository: UploadRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Validate, redact, and atomically stage one complete observation."""
    _require_enabled()
    request = _validated_chunk(owner, payload)
    prepared = _prepare_observation(request["observation"])
    now = _aware_now(clock())
    repo = repository or OrmUploadRepository()
    expired = False
    async with repo.transaction() as connection:
        session = await repo.lock_session(
            owner, request["upload_session_id"], connection
        )
        expired = await _check_open(session, repo, connection, now)
        if not expired:
            _require_session_resource(session, request)
            duplicate = await _stage_or_compare(
                repo, connection, session, owner, request, prepared, now
            )
    if expired:
        raise UploadError(UPLOAD_EXPIRED)
    return _validated_result(_chunk_result(request, duplicate), UPLOAD_CHUNK_RESPONSE_SCHEMA)


async def abort_upload_session(
    owner: UploadOwner,
    upload_session_id: str,
    *,
    repository: UploadRepository | None = None,
) -> dict[str, str]:
    """Move an owned OPEN session to ABORTED; cleanup remains asynchronous."""
    repo = repository or OrmUploadRepository()
    async with repo.transaction() as connection:
        session = await repo.lock_session(owner, upload_session_id, connection)
        if session is None:
            raise UploadError(UPLOAD_NOT_FOUND)
        if session.status == UploadSessionStatus.ABORTED:
            return {"upload_session_id": upload_session_id, "status": "ABORTED"}
        if session.status != UploadSessionStatus.OPEN:
            raise UploadError(UPLOAD_STATE_INVALID)
        session.status = UploadSessionStatus.ABORTED
        await repo.save_session(session, connection)
    return {"upload_session_id": upload_session_id, "status": "ABORTED"}


async def sweep_upload_sessions(
    *,
    repository: UploadRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    session_limit: int = DEFAULT_CLEANUP_LIMIT,
    staging_limit: int = DEFAULT_CLEANUP_LIMIT,
) -> UploadSweepResult:
    """Expire bounded OPEN sessions and soft-delete only their orphan staging."""
    if session_limit < 1 or staging_limit < 1:
        raise ValueError("Upload cleanup limits must be positive")
    repo = repository or OrmUploadRepository()
    session_ids, expired = await repo.cleanup_sessions(
        _aware_now(clock()), session_limit
    )
    cleaned = await repo.cleanup_staging(session_ids, staging_limit)
    return UploadSweepResult(expired, cleaned)


async def commit_upload_session(
    owner: UploadOwner,
    upload_session_id: str,
    *,
    expected_payload_hash: str | None = None,
    repository: UploadRepository | None = None,
    snapshot_repository=None,
    store=None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    id_factory: Callable[[], str] = lambda: str(uuid4()),
):
    """Freeze, assemble, prepare, and atomically commit one upload session."""
    from services.network_topology.upload_commit import commit_upload_session_impl

    return await commit_upload_session_impl(
        owner,
        upload_session_id,
        expected_payload_hash=expected_payload_hash,
        repository=repository,
        snapshot_repository=snapshot_repository,
        store=store,
        clock=clock,
        id_factory=id_factory,
        commit_snapshot=commit_prepared_snapshot,
    )


def validated_commit_request(payload: object) -> dict[str, Any]:
    return _contract_payload(payload, UPLOAD_COMMIT_SCHEMA)


def validated_commit_response(result) -> dict[str, str]:
    return _validated_result(result.as_dict(), UPLOAD_COMMIT_RESPONSE_SCHEMA)


def _validated_start(authority, payload) -> dict[str, Any]:
    request = _contract_payload(payload, UPLOAD_START_SCHEMA)
    _validate_authority(authority)
    envelope = request["snapshot_envelope_without_observations"]
    collector = envelope["collector"]
    if request["id"] != authority.topology_id:
        raise UploadError(MANIFEST_MISMATCH)
    if (collector["playbook_id"], collector["run_id"]) != (
        authority.playbook_id,
        authority.run_id,
    ):
        raise UploadError(MANIFEST_MISMATCH)
    return request


def _validated_chunk(owner, payload) -> dict[str, Any]:
    request = _contract_payload(payload, UPLOAD_CHUNK_SCHEMA)
    _validate_owner(owner)
    observation = request["observation"]
    size = len(canonicalize_json(observation))
    maximum = contracts.load_schema(observation["data_schema"]).get("x-max-bytes")
    if not isinstance(maximum, int) or size > maximum:
        raise UploadError(SCHEMA_INVALID)
    return request


def _contract_payload(payload, schema_uri) -> dict[str, Any]:
    try:
        contracts.validate_contract(payload, schema_uri)
    except contracts.ContractError:
        raise UploadError(SCHEMA_INVALID) from None
    return dict(payload)


def _validate_authority(authority) -> None:
    if type(authority) is not UploadAuthority:
        raise UploadError(UPLOAD_NOT_FOUND)
    values = (
        authority.tenant_id,
        authority.topology_id,
        authority.subject,
        authority.playbook_id,
        authority.run_id,
    )
    if any(not _is_text(value) for value in values):
        raise UploadError(UPLOAD_NOT_FOUND)


def _validate_owner(owner) -> None:
    if type(owner) is not UploadOwner:
        raise UploadError(UPLOAD_NOT_FOUND)
    if not _is_text(owner.tenant_id) or not _is_text(owner.subject):
        raise UploadError(UPLOAD_NOT_FOUND)


def _prepare_envelope(envelope):
    try:
        return prepare_stored_envelope(envelope)
    except (CanonicalizationError, TypeError, ValueError):
        raise UploadError(SCHEMA_INVALID) from None


def _prepare_observation(observation):
    try:
        return prepare_stored_envelope({"observation": observation})
    except (CanonicalizationError, TypeError, ValueError):
        raise UploadError(SCHEMA_INVALID) from None


def _validate_manifest(manifest, authority, request, now) -> None:
    if manifest is None:
        raise UploadError(MANIFEST_MISMATCH)
    if manifest.status != ManifestStatus.ISSUED or manifest.expires_at <= now:
        raise UploadError(MANIFEST_CONSUMED)
    owner = (manifest.creator_subject, manifest.playbook_id, manifest.run_id)
    expected_owner = (authority.subject, authority.playbook_id, authority.run_id)
    if owner != expected_owner:
        raise UploadError(MANIFEST_MISMATCH)
    target = request["snapshot_envelope_without_observations"]["target_manifest"]
    if not _manifest_row_matches(manifest, target):
        raise UploadError(MANIFEST_MISMATCH)
    _validate_frozen_resources(request, target)


def _manifest_row_matches(row, target) -> bool:
    try:
        canonical = canonicalize_and_hash_manifest(target)
        resources = orjson.loads(row.expected_resources_json)
    except (CanonicalizationError, orjson.JSONDecodeError, TypeError):
        return False
    fields = (
        (row.manifest_id, target["manifest_id"]),
        (row.manifest_hash, canonical.manifest_hash),
        (row.collection_sequence, target["collection_sequence"]),
        (row.base_graph_revision, target["base_graph_revision"]),
        (row.base_publication_revision, target["base_publication_revision"]),
        (row.profile_revision, target["profile_revision"]),
        (_iso(row.issued_at), target["issued_at"]),
        (_iso(row.expires_at), target["expires_at"]),
        (resources, target["expected_resources"]),
    )
    return all(left == right for left, right in fields)


def _validate_frozen_resources(request, target) -> None:
    envelope = request["snapshot_envelope_without_observations"]
    expected = [item["resource_id"] for item in target["expected_resources"]]
    resources = _envelope_resources(envelope)
    resource_ids = [item["resource_id"] for item in resources]
    completeness = envelope["completeness"]
    sets = (
        resource_ids,
        completeness["expected_resource_ids"],
        completeness["collected_resource_ids"],
    )
    if any(len(items) != len(set(items)) for items in sets):
        raise UploadError(MANIFEST_MISMATCH)
    if any(set(items) != set(expected) for items in sets):
        raise UploadError(MANIFEST_MISMATCH)


def _session_values(authority, request, prepared, manifest, upload_id, expires_at):
    envelope = prepared.normalized
    return {
        "tenant_id": authority.tenant_id,
        "topology_row_id": authority.topology_id,
        "manifest_row_id": str(manifest.id),
        "upload_id": _identifier(upload_id),
        "snapshot_id": envelope["snapshot_id"],
        "envelope_json": prepared.canonical_bytes.decode(),
        "envelope_hash": prepared.payload_hash,
        "status": UploadSessionStatus.OPEN,
        "client_expected_input_hash": request["client_expected_input_hash"],
        "expected_observation_count": sum(
            item["result"]["expected_observation_count"]
            for item in _envelope_resources(envelope)
        ),
        "authoritative_payload_hash": None,
        "submitter_subject": authority.subject,
        "playbook_id": authority.playbook_id,
        "run_id": authority.run_id,
        "expires_at": expires_at,
        "committed_at": None,
    }


async def _check_open(session, repo, connection, now) -> bool:
    if session is None:
        raise UploadError(UPLOAD_NOT_FOUND)
    if session.status != UploadSessionStatus.OPEN:
        raise UploadError(UPLOAD_STATE_INVALID)
    if session.expires_at > now:
        return False
    session.status = UploadSessionStatus.EXPIRED
    await repo.save_session(session, connection)
    return True


def _require_session_resource(session, request) -> None:
    try:
        envelope = orjson.loads(session.envelope_json)
    except (orjson.JSONDecodeError, TypeError):
        raise UploadError(UPLOAD_STATE_INVALID) from None
    resources = {item["resource_id"]: item for item in _envelope_resources(envelope)}
    resource = resources.get(request["resource_id"])
    if resource is None:
        raise UploadError(MANIFEST_MISMATCH)
    manifest_resources = {
        item["resource_id"]: item for item in envelope["target_manifest"]["expected_resources"]
    }
    allowed = manifest_resources.get(request["resource_id"])
    if allowed is None or request["observation"]["kind"] not in {
        *allowed["required_kinds"],
        *allowed["optional_kinds"],
    }:
        raise UploadError(MANIFEST_MISMATCH)


async def _stage_or_compare(
    repo, connection, session, owner, request, prepared, now
) -> bool:
    observation_id = request["observation"]["observation_id"]
    existing = await repo.find_staging(
        owner.tenant_id, session.id, observation_id, connection
    )
    if existing is not None:
        if existing.content_hash != prepared.payload_hash:
            raise UploadError(UPLOAD_CONFLICT)
        return True
    await repo.create_staging(
        connection,
        tenant_id=owner.tenant_id,
        topology_row_id=str(session.topology_row_id),
        upload_session_row_id=str(session.id),
        observation_id=observation_id,
        resource_id=request["resource_id"],
        content_hash=prepared.payload_hash,
        redacted_sha256=prepared.payload_hash,
        observation_payload=prepared.canonical_bytes.decode(),
        size_bytes=len(prepared.canonical_bytes),
        received_at=now,
    )
    return False


def _envelope_resources(envelope) -> list[dict[str, Any]]:
    return [
        resource
        for source in envelope["sources"]
        for resource in source["resources"]
    ]


def _start_result(row) -> dict[str, Any]:
    return {
        "upload_session_id": row.upload_id,
        "status": "OPEN",
        "expires_at": _iso(row.expires_at),
    }


def _chunk_result(request, duplicate) -> dict[str, Any]:
    return {
        "upload_session_id": request["upload_session_id"],
        "observation_id": request["observation"]["observation_id"],
        "status": "ACCEPTED",
        "duplicate": duplicate,
    }


def _validated_result(data: dict[str, Any], schema_uri: str) -> dict[str, Any]:
    contracts.validate_contract({"code": 0, "data": data}, schema_uri)
    return data


def _require_enabled() -> None:
    if not settings.TOPOLOGY_V2_INGRESS_ENABLED:
        raise UploadError(UPLOAD_DISABLED)


def _identifier(value: object) -> str:
    if not _is_text(value):
        raise UploadError(SCHEMA_INVALID)
    return value


def _is_text(value: object) -> bool:
    return isinstance(value, str) and value.strip() == value and bool(value)


def _aware_now(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Upload timestamps must be timezone-aware")
    return value


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
