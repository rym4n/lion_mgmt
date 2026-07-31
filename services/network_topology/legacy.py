"""Lifecycle and publication guards for explicit legacy-to-v2 upgrades."""

from __future__ import annotations

from collections.abc import Callable, Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from services.network_topology import compatibility_manager as compatibility
from services.network_topology.publishing import (
    OrmPublishingRepository,
    PublicationRequest,
    PublishingError,
    publish_graph,
)

ACTIVE = "ACTIVE"
CANCELLED = "CANCELLED"
CONSUMED = "CONSUMED"
EXPIRED = "EXPIRED"

LEGACY_CONVERSION_REQUIRED = compatibility.LEGACY_CONVERSION_REQUIRED
UPGRADE_PERMISSION_DENIED = "UPGRADE_PERMISSION_DENIED"
UPGRADE_REQUEST_INVALID = "UPGRADE_REQUEST_INVALID"
UPGRADE_TOPOLOGY_NOT_FOUND = "UPGRADE_TOPOLOGY_NOT_FOUND"
UPGRADE_LEGACY_STATE_INVALID = "UPGRADE_LEGACY_STATE_INVALID"
UPGRADE_NOT_REQUIRED = "UPGRADE_NOT_REQUIRED"
UPGRADE_REVISION_CONFLICT = "UPGRADE_REVISION_CONFLICT"
UPGRADE_INTENT_INVALID = "UPGRADE_INTENT_INVALID"

_UPDATE_PERMISSION = "network_topology:u"


def _default_clock():
    return datetime.now(timezone.utc).replace(microsecond=0)


class LegacyUpgradeError(RuntimeError):
    """Stable failure raised without exposing legacy graph contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class UpgradeAuthority:
    tenant_id: str
    subject: str
    permissions: Collection[str]


@dataclass(frozen=True, slots=True)
class UpgradeIntentRequest:
    topology_id: str
    expected_legacy_revision: int
    expires_at: datetime


class LegacyRepository(Protocol):
    def transaction(self): ...
    async def lock_topology(self, tenant_id, topology_id, connection): ...
    async def lock_current_version(self, topology, connection): ...
    async def lock_active_intents(self, topology, connection): ...
    async def create_intent(self, values, connection): ...
    async def lock_intent(
        self, tenant_id, topology_id, intent_id, connection
    ): ...
    async def save_intent(self, intent, connection): ...
    async def lock_expirable_intents(self, now, connection): ...


async def create_upgrade_intent(
    authority: UpgradeAuthority,
    request: UpgradeIntentRequest,
    repository: LegacyRepository | None = None,
    *,
    clock: Callable[[], datetime] = _default_clock,
):
    """Replace the ACTIVE intent for one exact current legacy version."""
    now = _validate_create(authority, request, clock)
    repo = repository or OrmLegacyRepository()
    async with repo.transaction() as connection:
        topology = await repo.lock_topology(
            authority.tenant_id, request.topology_id, connection
        )
        if topology is None:
            raise LegacyUpgradeError(UPGRADE_TOPOLOGY_NOT_FOUND)
        version = await repo.lock_current_version(topology, connection)
        _validate_legacy_cas(topology, version, request.expected_legacy_revision)
        active = await repo.lock_active_intents(topology, connection)
        if type(active) is not list or len(active) > 1:
            raise LegacyUpgradeError(UPGRADE_INTENT_INVALID)
        if active:
            _cancel_row(active[0], authority.subject, now, "REPLACED")
            await repo.save_intent(active[0], connection)
        return await repo.create_intent(
            _intent_values(authority, request, topology, version), connection
        )


async def cancel_upgrade_intent(
    authority: UpgradeAuthority,
    topology_id: str,
    intent_id: str,
    reason: str,
    repository: LegacyRepository | None = None,
    *,
    clock: Callable[[], datetime] = _default_clock,
):
    """Cancel under the same topology-to-intent lock order used by publish."""
    _validate_authority(authority)
    now = _read_now(clock)
    if not _text(topology_id) or not _text(intent_id) or not _reason(reason):
        raise LegacyUpgradeError(UPGRADE_REQUEST_INVALID)
    repo = repository or OrmLegacyRepository()
    async with repo.transaction() as connection:
        topology, intent = await repo.lock_intent(
            authority.tenant_id, topology_id, intent_id, connection
        )
        if topology is None:
            raise LegacyUpgradeError(UPGRADE_TOPOLOGY_NOT_FOUND)
        if not _active_intent(intent, topology, now):
            raise LegacyUpgradeError(UPGRADE_INTENT_INVALID)
        _cancel_row(intent, authority.subject, now, reason)
        await repo.save_intent(intent, connection)
        return intent


async def expire_upgrade_intents(
    repository: LegacyRepository | None = None,
    *,
    clock: Callable[[], datetime] = _default_clock,
) -> int:
    """Move elapsed ACTIVE intents to the terminal EXPIRED state."""
    now = _read_now(clock)
    repo = repository or OrmLegacyRepository()
    async with repo.transaction() as connection:
        rows = await repo.lock_expirable_intents(now, connection)
        if type(rows) is not list:
            raise LegacyUpgradeError(UPGRADE_INTENT_INVALID)
        for intent in rows:
            intent.status = EXPIRED
            await repo.save_intent(intent, connection)
        return len(rows)


def validate_manifest_upgrade(topology, version, intent, now: datetime):
    """Return the intent for non-blank legacy data or allow exact blank data."""
    if compatibility.is_blank_legacy_topology(topology, version):
        return None
    if intent is None:
        raise LegacyUpgradeError(LEGACY_CONVERSION_REQUIRED)
    if not _active_intent(intent, topology, now):
        raise LegacyUpgradeError(UPGRADE_INTENT_INVALID)
    valid = (
        getattr(intent, "legacy_graph_version_row_id", None)
        == getattr(version, "id", None)
        and getattr(intent, "expected_legacy_revision", None)
        == getattr(topology, "revision", None)
    )
    if not valid:
        raise LegacyUpgradeError(UPGRADE_INTENT_INVALID)
    return intent


async def publish_upgrade_graph(
    request: PublicationRequest,
    repository=None,
    object_store=None,
    *,
    inline_threshold: int = 256 * 1024,
    clock: Callable[[], datetime] = _default_clock,
):
    """Publish through an adapter that binds the run to its original intent."""
    base = repository or OrmPublishingRepository()
    guarded = UpgradePublicationRepository(base, clock)
    return await publish_graph(
        request,
        guarded,
        object_store,
        inline_threshold=inline_threshold,
        clock=clock,
    )


class UpgradePublicationRepository:
    """Add immutable Snapshot/Manifest intent binding to a publishing repo."""

    def __init__(self, repository, clock=_default_clock) -> None:
        self.repository = repository
        self.clock = clock
        self._publication = None
        self._manifest = None

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def transaction(self):
        return self.repository.transaction()

    async def lock_publication(self, request, connection):
        locked = await self.repository.lock_publication(request, connection)
        self._publication = locked
        self._manifest = await _locked_manifest(
            self.repository, locked[2], connection
        )
        return locked

    async def lock_upgrade_intent(self, request, connection):
        intent = await self.repository.lock_upgrade_intent(request, connection)
        if not _publication_binding_valid(
            request, self._publication, self._manifest, intent, _read_now(self.clock)
        ):
            raise PublishingError(UPGRADE_INTENT_INVALID)
        return intent


def _validate_create(authority, request, clock):
    _validate_authority(authority)
    now = _read_now(clock)
    valid = (
        type(request) is UpgradeIntentRequest
        and _text(request.topology_id)
        and type(request.expected_legacy_revision) is int
        and request.expected_legacy_revision > 0
        and isinstance(request.expires_at, datetime)
        and request.expires_at.tzinfo is not None
        and request.expires_at > now
    )
    if not valid:
        raise LegacyUpgradeError(UPGRADE_REQUEST_INVALID)
    return now


def _validate_authority(authority):
    valid = (
        type(authority) is UpgradeAuthority
        and _text(authority.tenant_id)
        and _text(authority.subject)
        and isinstance(authority.permissions, Collection)
    )
    if not valid:
        raise LegacyUpgradeError(UPGRADE_PERMISSION_DENIED)
    if not any(_grants_update(value) for value in authority.permissions):
        raise LegacyUpgradeError(UPGRADE_PERMISSION_DENIED)


def _validate_legacy_cas(topology, version, expected_revision):
    pair = (
        getattr(topology, "document_schema_version", None),
        getattr(topology, "generation_source", None),
    )
    if pair != (compatibility.LEGACY_SCHEMA_VERSION, "manual"):
        raise LegacyUpgradeError(UPGRADE_LEGACY_STATE_INVALID)
    actual = getattr(topology, "revision", None)
    current = getattr(topology, "current_graph_revision", None)
    valid = (
        actual == expected_revision
        and current == expected_revision
        and version is not None
        and getattr(version, "version", None) == expected_revision
        and getattr(version, "id", None)
        == getattr(topology, "current_graph_version_row_id", None)
        and getattr(version, "topology_id", None) == getattr(topology, "id", None)
        and getattr(version, "tenant_id", None) == getattr(topology, "tenant_id", None)
        and getattr(version, "document_schema_version", None) == "legacy/v1"
    )
    if not valid:
        raise LegacyUpgradeError(UPGRADE_REVISION_CONFLICT)


def _intent_values(authority, request, topology, version):
    return {
        "tenant_id": authority.tenant_id,
        "topology_row_id": topology.id,
        "legacy_graph_version_row_id": version.id,
        "expected_legacy_revision": request.expected_legacy_revision,
        "status": ACTIVE,
        "created_by": authority.subject,
        "expires_at": request.expires_at,
        "consumed_at": None,
        "consumed_by": None,
        "cancelled_at": None,
        "cancelled_by": None,
        "cancellation_reason": None,
    }


def _cancel_row(intent, subject, now, reason):
    intent.status = CANCELLED
    intent.cancelled_at = now
    intent.cancelled_by = subject
    intent.cancellation_reason = reason


def _active_intent(intent, topology, now):
    if intent is None or getattr(intent, "status", None) != ACTIVE:
        return False
    valid = (
        getattr(intent, "tenant_id", None) == getattr(topology, "tenant_id", None)
        and getattr(intent, "topology_row_id", None) == getattr(topology, "id", None)
        and getattr(intent, "consumed_at", None) is None
        and getattr(intent, "cancelled_at", None) is None
        and getattr(intent, "deleted", None) is False
    )
    try:
        return bool(valid and intent.expires_at > now)
    except (AttributeError, TypeError):
        return False


async def _locked_manifest(repository, snapshot, connection):
    related = getattr(snapshot, "__dict__", {}).get("target_manifest_row")
    if related is not None:
        return related
    method = getattr(repository, "lock_manifest_for_snapshot", None)
    if method is not None:
        return await method(snapshot, connection)
    from models import TopologyCollectionManifest

    return await TopologyCollectionManifest.filter(
        id=getattr(snapshot, "target_manifest_row_id", None),
        tenant_id=getattr(snapshot, "tenant_id", None),
        deleted=False,
    ).using_db(connection).select_for_update().first()


def _publication_binding_valid(request, publication, manifest, intent, now):
    if publication is None or manifest is None:
        return False
    topology, run, snapshot, _ = publication
    expected_id = request.upgrade_intent_id
    manifest_id = getattr(manifest, "upgrade_intent_row_id", None)
    valid = (
        getattr(manifest, "status", None) == "COMMITTED"
        and _same_optional_id(manifest_id, expected_id)
        and _same_optional_id(
            getattr(snapshot, "target_manifest_row_id", None),
            getattr(manifest, "id", None),
        )
        and str(getattr(manifest, "committed_snapshot_row_id", ""))
        == str(snapshot.id)
        and str(getattr(manifest, "committed_analysis_run_row_id", ""))
        == str(run.id)
    )
    if expected_id is None:
        return valid and intent is None
    return (
        valid
        and str(getattr(intent, "id", "")) == str(expected_id)
        and _active_intent(intent, topology, now)
    )


def _same_optional_id(left, right):
    if left is None or right is None:
        return left is right
    return str(left) == str(right)


def _read_now(clock):
    try:
        value = clock()
    except Exception:
        raise LegacyUpgradeError(UPGRADE_REQUEST_INVALID) from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LegacyUpgradeError(UPGRADE_REQUEST_INVALID)
    return value.astimezone(timezone.utc)


def _text(value, maximum=255):
    return type(value) is str and value == value.strip() and 0 < len(value) <= maximum


def _reason(value):
    return _text(value, 1024)


def _grants_update(value):
    if value in {_UPDATE_PERMISSION, "network_topology:*", "*:*"}:
        return True
    if not isinstance(value, str) or not value.startswith("network_topology:"):
        return False
    return "u" in {item.strip() for item in value.split(":", 1)[1].split(",")}


class OrmLegacyRepository:
    @asynccontextmanager
    async def transaction(self):
        from tortoise.transactions import in_transaction

        async with in_transaction() as connection:
            yield connection

    async def lock_topology(self, tenant_id, topology_id, connection):
        from models import NetworkTopology

        return await NetworkTopology.filter(
            id=topology_id, tenant_id=tenant_id, deleted=False
        ).using_db(connection).select_for_update().first()

    async def lock_current_version(self, topology, connection):
        from models import NetworkTopologyVersion

        return await NetworkTopologyVersion.filter(
            id=topology.current_graph_version_row_id,
            topology_id=topology.id,
            tenant_id=topology.tenant_id,
            deleted=False,
        ).using_db(connection).select_for_update().first()

    async def lock_active_intents(self, topology, connection):
        from models import TopologyUpgradeIntent

        return await TopologyUpgradeIntent.filter(
            tenant_id=topology.tenant_id,
            topology_row_id=topology.id,
            status=ACTIVE,
            deleted=False,
        ).using_db(connection).select_for_update().limit(2)

    async def create_intent(self, values, connection):
        from models import TopologyUpgradeIntent

        return await TopologyUpgradeIntent.create(using_db=connection, **values)

    async def lock_intent(self, tenant_id, topology_id, intent_id, connection):
        topology = await self.lock_topology(tenant_id, topology_id, connection)
        if topology is None:
            return None, None
        from models import TopologyUpgradeIntent

        intent = await TopologyUpgradeIntent.filter(
            id=intent_id,
            tenant_id=tenant_id,
            topology_row_id=topology_id,
            deleted=False,
        ).using_db(connection).select_for_update().first()
        return topology, intent

    async def save_intent(self, intent, connection):
        await intent.save(
            using_db=connection,
            update_fields=[
                "status", "cancelled_at", "cancelled_by",
                "cancellation_reason", "utime",
            ],
        )

    async def lock_expirable_intents(self, now, connection):
        from models import TopologyUpgradeIntent

        return await TopologyUpgradeIntent.filter(
            status=ACTIVE, expires_at__lte=now, deleted=False
        ).using_db(connection).select_for_update().all()
