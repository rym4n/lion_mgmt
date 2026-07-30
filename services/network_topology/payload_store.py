"""Shared encrypted payload preparation for direct and chunked topology sync."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

import aioboto3
from botocore.config import Config
from tortoise.transactions import in_transaction

from libs.utils import decrypt_aes256_gcm, encrypt_aes256_gcm
from models._network_topology_v2_analysis import TopologyAnalysisRun
from models._network_topology_v2_base import PayloadObjectStatus
from models._network_topology_v2_collection import (
    TopologyCollectionSnapshot,
    TopologyPayloadObject,
)
from services.network_topology.canonicalization import (
    CanonicalSnapshot,
    canonicalize_and_hash_snapshot,
)
from services.network_topology.redaction import validate_then_redact
from settings import FS_AK, FS_SK, RY_SYS_BUCKET, S3_HOST


DEFAULT_INLINE_THRESHOLD = 256 * 1024
DEFAULT_READY_TTL = timedelta(hours=1)
DEFAULT_BUCKET = RY_SYS_BUCKET
STAGING_ROOT = "network-topology/staging"
_TENANT_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


class PayloadStoreError(RuntimeError):
    """Stable payload lifecycle failure without payload-bearing details."""


@dataclass(frozen=True)
class PreparedEnvelope:
    normalized: dict[str, Any]
    canonical_bytes: bytes
    payload_hash: str


@dataclass(frozen=True)
class PreparedPayload:
    tenant_id: str
    payload_hash: str
    size_bytes: int
    inline_payload: str | None = None
    payload_object_id: str | None = None

    @property
    def is_inline(self) -> bool:
        return self.payload_object_id is None

    @property
    def redacted_payload(self) -> str | None:
        """Value intended for TopologyCollectionSnapshot.redacted_payload."""
        return self.inline_payload


@dataclass(frozen=True)
class StagingObject:
    object_key: str
    created_at: datetime


@dataclass(frozen=True)
class SweepResult:
    deleted_records: int = 0
    deleted_staging_objects: int = 0


class PayloadCodec(Protocol):
    encryption_version: str
    key_version: str

    def encrypt(self, value: bytes) -> bytes: ...

    def decrypt(self, value: bytes) -> bytes: ...


class ObjectBackend(Protocol):
    async def put_staging(
        self, tenant_id: str, object_key: str, value: bytes, created_at: datetime
    ) -> None: ...

    async def read(self, object_key: str) -> bytes: ...

    async def delete(self, object_key: str) -> None: ...

    async def list_staging(self, tenant_id: str) -> list[StagingObject]: ...


class PayloadRepository(Protocol):
    async def create_staging(self, **values: Any) -> Any: ...

    async def mark_ready(
        self, row: Any, *, verified_at: datetime, retention_until: datetime
    ) -> Any: ...

    async def lock(self, tenant_id: str, object_id: str, connection: Any) -> Any: ...

    async def save(self, row: Any, connection: Any) -> None: ...

    async def expired_orphans(self, tenant_id: str, before: datetime) -> list[Any]: ...

    async def mark_deleting_if_unreferenced(
        self, tenant_id: str, object_id: str
    ) -> Any: ...

    async def soft_delete(self, row: Any) -> None: ...

    async def has_object_key(self, tenant_id: str, object_key: str) -> bool: ...


def prepare_stored_envelope(raw_envelope: dict[str, Any]) -> PreparedEnvelope:
    """Return the redacted JCS envelope used by both direct and chunk commit."""
    redacted = validate_then_redact(raw_envelope)
    canonical: CanonicalSnapshot = canonicalize_and_hash_snapshot(redacted)
    return PreparedEnvelope(
        normalized=canonical.normalized,
        canonical_bytes=canonical.canonical_bytes,
        payload_hash=canonical.payload_hash,
    )


class AesPayloadCodec:
    encryption_version = "aes-256-gcm/v1"
    key_version = "RY_RES_KEY/current"

    def encrypt(self, value: bytes) -> bytes:
        return encrypt_aes256_gcm(value).encode()

    def decrypt(self, value: bytes) -> bytes:
        return decrypt_aes256_gcm(value.decode()).encode()


class RustFsPayloadBackend:
    def __init__(self, bucket: str = DEFAULT_BUCKET):
        self.bucket = bucket

    async def put_staging(
        self, tenant_id: str, object_key: str, value: bytes, created_at: datetime
    ) -> None:
        del tenant_id, created_at
        try:
            async with self._client() as client:
                await client.put_object(
                    Bucket=self.bucket,
                    Key=object_key,
                    Body=value,
                    ContentType="application/octet-stream",
                    IfNoneMatch="*",
                )
        except Exception as exc:
            raise PayloadStoreError("Payload staging write failed") from exc

    async def read(self, object_key: str) -> bytes:
        try:
            async with self._client() as client:
                response = await client.get_object(Bucket=self.bucket, Key=object_key)
                return await response["Body"].read()
        except Exception as exc:
            raise PayloadStoreError("Payload staging read failed") from exc

    async def delete(self, object_key: str) -> None:
        try:
            async with self._client() as client:
                await client.delete_object(Bucket=self.bucket, Key=object_key)
        except Exception as exc:
            raise PayloadStoreError("Payload staging delete failed") from exc

    async def list_staging(self, tenant_id: str) -> list[StagingObject]:
        prefix = _staging_prefix(tenant_id)
        objects: list[StagingObject] = []
        token = None
        while True:
            page = await self._list_page(prefix, token)
            objects.extend(_staging_objects(page))
            token = page.get("NextContinuationToken")
            if not token:
                return objects

    async def _list_page(self, prefix: str, token: str | None) -> dict[str, Any]:
        params = {"Bucket": self.bucket, "Prefix": prefix}
        if token:
            params["ContinuationToken"] = token
        try:
            async with self._client() as client:
                return await client.list_objects_v2(**params)
        except Exception as exc:
            raise PayloadStoreError("Payload staging list failed") from exc

    def _client(self):
        return aioboto3.Session().client(
            "s3",
            endpoint_url=S3_HOST,
            aws_access_key_id=FS_AK,
            aws_secret_access_key=FS_SK,
            config=Config(signature_version="s3v4"),
        )


class OrmPayloadRepository:
    async def create_staging(self, **values: Any) -> Any:
        return await TopologyPayloadObject.create(**values)

    async def mark_ready(
        self, row: Any, *, verified_at: datetime, retention_until: datetime
    ) -> Any:
        async with in_transaction() as connection:
            locked = await self.lock(row.tenant_id, row.id, connection)
            if locked is None or locked.status != PayloadObjectStatus.STAGING:
                raise PayloadStoreError("Payload staging claim is no longer available")
            locked.status = PayloadObjectStatus.READY
            locked.verified_at = verified_at
            locked.retention_until = retention_until
            await self.save(locked, connection)
            return locked

    async def lock(self, tenant_id: str, object_id: str, connection: Any) -> Any:
        query = TopologyPayloadObject.filter(
            id=object_id, tenant_id=tenant_id, deleted=False
        )
        return await query.using_db(connection).select_for_update().first()

    async def save(self, row: Any, connection: Any) -> None:
        await row.save(using_db=connection)

    async def expired_orphans(self, tenant_id: str, before: datetime) -> list[Any]:
        return await TopologyPayloadObject.filter(
            tenant_id=tenant_id,
            deleted=False,
            status__in=(
                PayloadObjectStatus.STAGING,
                PayloadObjectStatus.READY,
                PayloadObjectStatus.DELETING,
            ),
            retention_until__lte=before,
        )

    async def mark_deleting_if_unreferenced(
        self, tenant_id: str, object_id: str
    ) -> Any:
        async with in_transaction() as connection:
            row = await self.lock(tenant_id, object_id, connection)
            if row is not None and row.status == PayloadObjectStatus.DELETING:
                return row
            if not _is_collectable_ready(row):
                return None
            if await self._has_references(tenant_id, object_id, connection):
                return None
            row.status = PayloadObjectStatus.DELETING
            await self.save(row, connection)
            return row

    async def soft_delete(self, row: Any) -> None:
        row.deleted = True
        await row.save(update_fields=("deleted", "utime"))

    async def has_object_key(self, tenant_id: str, object_key: str) -> bool:
        rows = await TopologyPayloadObject.filter(
            tenant_id=tenant_id, deleted=False
        ).all()
        return any(row.object_key == object_key for row in rows)

    async def _has_references(
        self, tenant_id: str, object_id: str, connection: Any
    ) -> bool:
        snapshot = TopologyCollectionSnapshot.filter(
            tenant_id=tenant_id,
            deleted=False,
            payload_object_row_id=object_id,
        ).using_db(connection)
        if await snapshot.exists():
            return True
        return await _analysis_reference_exists(tenant_id, object_id, connection)


class PayloadStore:
    def __init__(
        self,
        backend: ObjectBackend | None = None,
        repository: PayloadRepository | None = None,
        codec: PayloadCodec | None = None,
        inline_threshold: int = DEFAULT_INLINE_THRESHOLD,
        ready_ttl: timedelta = DEFAULT_READY_TTL,
    ):
        if inline_threshold < 0 or ready_ttl <= timedelta(0):
            raise ValueError("Payload storage limits must be positive")
        self.backend = backend or RustFsPayloadBackend()
        self.repository = repository or OrmPayloadRepository()
        self.codec = codec or AesPayloadCodec()
        self.inline_threshold = inline_threshold
        self.ready_ttl = ready_ttl

    async def prepare(
        self,
        tenant_id: str,
        raw_envelope: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> PreparedPayload:
        _validate_tenant(tenant_id)
        timestamp = _utc_now(now)
        envelope = prepare_stored_envelope(raw_envelope)
        if len(envelope.canonical_bytes) <= self.inline_threshold:
            return _inline_payload(tenant_id, envelope)
        return await self._prepare_object(tenant_id, envelope, timestamp)

    async def _prepare_object(
        self, tenant_id: str, envelope: PreparedEnvelope, now: datetime
    ) -> PreparedPayload:
        object_key = _new_object_key(tenant_id)
        encrypted = self.codec.encrypt(envelope.canonical_bytes)
        row = await self.repository.create_staging(
            **_staging_values(self, tenant_id, envelope, object_key, now)
        )
        await self.backend.put_staging(tenant_id, object_key, encrypted, now)
        await self._verify_object(object_key, envelope.canonical_bytes)
        row = await self.repository.mark_ready(
            row, verified_at=now, retention_until=now + self.ready_ttl
        )
        return _object_payload(tenant_id, envelope, row.id)

    async def _verify_object(self, object_key: str, expected: bytes) -> None:
        encrypted = await self.backend.read(object_key)
        try:
            actual = self.codec.decrypt(encrypted)
        except Exception as exc:
            raise PayloadStoreError("Payload staging decrypt verification failed") from exc
        if len(actual) != len(expected) or actual != expected:
            raise PayloadStoreError("Payload staging integrity verification failed")

    async def activate(
        self,
        tenant_id: str,
        prepared: PreparedPayload,
        *,
        connection: Any,
        now: datetime | None = None,
    ) -> Any:
        _validate_tenant(tenant_id)
        if tenant_id != prepared.tenant_id:
            raise ValueError("Prepared payload tenant does not match caller tenant")
        if prepared.is_inline:
            return None
        row = await self.repository.lock(
            tenant_id, prepared.payload_object_id, connection
        )
        if row is None or row.status != PayloadObjectStatus.READY:
            raise ValueError("Payload object must be READY before business reference")
        if row.content_hash != prepared.payload_hash or row.size_bytes != prepared.size_bytes:
            raise PayloadStoreError("Prepared payload metadata mismatch")
        row.status = PayloadObjectStatus.LIVE
        row.reference_count += 1
        row.last_referenced_at = _utc_now(now)
        await self.repository.save(row, connection)
        return row

    async def sweep_orphans(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> SweepResult:
        _validate_tenant(tenant_id)
        timestamp = _utc_now(now)
        records = await self._sweep_ready(tenant_id, timestamp)
        staging = await self._sweep_staging(tenant_id, timestamp)
        return SweepResult(records, staging)

    async def _sweep_ready(self, tenant_id: str, now: datetime) -> int:
        deleted = 0
        for candidate in await self.repository.expired_orphans(tenant_id, now):
            row = await self.repository.mark_deleting_if_unreferenced(
                tenant_id, candidate.id
            )
            if row is None:
                continue
            await self.backend.delete(row.object_key)
            await self.repository.soft_delete(row)
            deleted += 1
        return deleted

    async def _sweep_staging(self, tenant_id: str, now: datetime) -> int:
        deleted = 0
        deadline = now - self.ready_ttl
        for item in await self.backend.list_staging(tenant_id):
            if item.created_at > deadline:
                continue
            if await self.repository.has_object_key(tenant_id, item.object_key):
                continue
            await self.backend.delete(item.object_key)
            deleted += 1
        return deleted


def _inline_payload(tenant_id: str, envelope: PreparedEnvelope) -> PreparedPayload:
    return PreparedPayload(
        tenant_id=tenant_id,
        payload_hash=envelope.payload_hash,
        size_bytes=len(envelope.canonical_bytes),
        inline_payload=envelope.canonical_bytes.decode(),
    )


def _object_payload(
    tenant_id: str, envelope: PreparedEnvelope, object_id: str
) -> PreparedPayload:
    return PreparedPayload(
        tenant_id=tenant_id,
        payload_hash=envelope.payload_hash,
        size_bytes=len(envelope.canonical_bytes),
        payload_object_id=object_id,
    )


def _staging_values(
    store: PayloadStore,
    tenant_id: str,
    envelope: PreparedEnvelope,
    object_key: str,
    now: datetime,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "content_hash": envelope.payload_hash,
        "object_key": object_key,
        "encryption_version": store.codec.encryption_version,
        "key_version": store.codec.key_version,
        "size_bytes": len(envelope.canonical_bytes),
        "status": PayloadObjectStatus.STAGING,
        "reference_count": 0,
        "verified_at": None,
        "retention_until": now + store.ready_ttl,
    }


def _new_object_key(tenant_id: str) -> str:
    return f"{_staging_prefix(tenant_id)}{uuid4().hex}.payload"


def _staging_prefix(tenant_id: str) -> str:
    _validate_tenant(tenant_id)
    return f"{STAGING_ROOT}/{tenant_id}/"


def _validate_tenant(tenant_id: str) -> None:
    if not isinstance(tenant_id, str) or _TENANT_SEGMENT.fullmatch(tenant_id) is None:
        raise ValueError("Invalid tenant identifier")


def _utc_now(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Payload timestamps must be timezone-aware")
    return timestamp


def _staging_objects(page: dict[str, Any]) -> list[StagingObject]:
    return [
        StagingObject(item["Key"], item["LastModified"])
        for item in page.get("Contents", ())
    ]


def _is_collectable_ready(row: Any) -> bool:
    return bool(
        row is not None
        and row.status in {PayloadObjectStatus.STAGING, PayloadObjectStatus.READY}
        and row.reference_count == 0
    )


async def _analysis_reference_exists(
    tenant_id: str, object_id: str, connection: Any
) -> bool:
    common = {"tenant_id": tenant_id, "deleted": False}
    facts = TopologyAnalysisRun.filter(
        **common, fact_graph_payload_object_row_id=object_id
    ).using_db(connection)
    if await facts.exists():
        return True
    reports = TopologyAnalysisRun.filter(
        **common, validation_report_object_row_id=object_id
    ).using_db(connection)
    return await reports.exists()
