"""Database leases, fencing, state transitions, and crash recovery."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol

from tortoise.expressions import Q
from tortoise.transactions import in_transaction

import settings
from models._network_topology_v2_analysis import TopologyAnalysisRun
from models._network_topology_v2_base import (
    AnalysisRunStage,
    AnalysisRunState,
    PayloadObjectStatus,
)
from models._network_topology_v2_collection import (
    TopologyCollectionSnapshot,
    TopologyPayloadObject,
    TopologyProfileRevision,
)
from models.network_topology import NetworkTopology
from services.network_topology.analysis_runs import (
    TERMINAL_STATES,
    _PUBLIC_SUMMARIES,
    classify_error,
    worker_write_allowed,
)


DEFAULT_LEASE_TTL = timedelta(seconds=60)
DEFAULT_RETRY_BASE = timedelta(seconds=5)
DEFAULT_RETRY_CAP = timedelta(minutes=5)
CLAIM_SCAN_LIMIT = 32
MAX_FENCING_TOKEN = 2**63 - 1

STAGE_ORDER = (
    AnalysisRunStage.SPLITTING,
    AnalysisRunStage.PARSING,
    AnalysisRunStage.AGENT_NORMALIZING,
    AnalysisRunStage.VALIDATING_FACTS,
    AnalysisRunStage.FACTS_READY,
    AnalysisRunStage.RESOLVING_IDENTITIES,
    AnalysisRunStage.BUILDING_SCOPES,
    AnalysisRunStage.PROJECTING_GRAPH,
    AnalysisRunStage.VALIDATING_GRAPH,
    AnalysisRunStage.PUBLISHING,
)
STAGE_TRANSITIONS = {
    current: following for current, following in zip(STAGE_ORDER, STAGE_ORDER[1:])
}


class InvalidTransition(RuntimeError):
    """The requested durable stage transition violates the frozen order."""


@dataclass(frozen=True, slots=True)
class LeaseIdentity:
    run_id: str
    worker_id: str
    lease_generation: int
    fencing_token: int


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    run: Any
    identity: LeaseIdentity


@dataclass(frozen=True, slots=True)
class GuardContext:
    topology: Any
    snapshot: Any
    profile: Any
    current_snapshot: Any | None


class LeaseRepository(Protocol):
    def transaction(self): ...

    async def claim_candidates(self, now, limit, *, connection): ...

    async def payload_ready(self, run, *, connection): ...

    async def get_run_for_update(self, run_id, *, connection): ...

    async def save_run(self, run, fields, *, connection): ...

    async def get_guard_context(self, run, *, connection): ...


class OrmLeaseRepository:
    @asynccontextmanager
    async def transaction(self):
        async with in_transaction() as connection:
            yield connection

    async def claim_candidates(self, now, limit, *, connection):
        due = _due_query(now, settings.TOPOLOGY_V2_PUBLISH_ENABLED)
        payload_ready = Q(snapshot_row__redacted_payload__isnull=False) | Q(
            snapshot_row__payload_object_row__status=PayloadObjectStatus.LIVE
        )
        query = TopologyAnalysisRun.filter(
            Q(cancellation_requested=True) | (due & payload_ready),
            deleted=False,
        ).exclude(state__in=tuple(TERMINAL_STATES))
        return await (
            query.using_db(connection)
            .select_for_update(
                skip_locked=True,
                of=(TopologyAnalysisRun._meta.db_table,),
            )
            .order_by("ctime")
            .limit(limit)
        )

    async def payload_ready(self, run, *, connection):
        snapshot = await TopologyCollectionSnapshot.filter(
            id=run.snapshot_row_id,
            tenant_id=run.tenant_id,
            deleted=False,
        ).using_db(connection).first()
        if snapshot is None:
            return False
        if snapshot.redacted_payload is not None:
            return True
        if snapshot.payload_object_row_id is None:
            return False
        return await TopologyPayloadObject.filter(
            id=snapshot.payload_object_row_id,
            tenant_id=run.tenant_id,
            deleted=False,
            status=PayloadObjectStatus.LIVE,
        ).using_db(connection).exists()

    async def get_run_for_update(self, run_id, *, connection):
        return await TopologyAnalysisRun.filter(
            id=run_id, deleted=False
        ).using_db(connection).select_for_update().first()

    async def save_run(self, run, fields, *, connection):
        await run.save(using_db=connection, update_fields=[*fields, "utime"])

    async def get_guard_context(self, run, *, connection):
        topology = await self._topology(run, connection)
        snapshot = await self._snapshot(run, connection)
        profile = await self._profile(run, connection)
        current_snapshot = await self._current_snapshot(topology, run, connection)
        return GuardContext(topology, snapshot, profile, current_snapshot)

    async def due_runs_for_alert(self, now, limit=CLAIM_SCAN_LIMIT):
        rows = await TopologyAnalysisRun.filter(deleted=False).exclude(
            state__in=tuple(TERMINAL_STATES)
        ).order_by("ctime").limit(limit)
        return [row for row in rows if run_is_due(row, now, publish_enabled=False)]

    async def _topology(self, run, connection):
        return await NetworkTopology.filter(
            id=run.topology_row_id,
            tenant_id=run.tenant_id,
            deleted=False,
        ).using_db(connection).select_for_update().first()

    async def _snapshot(self, run, connection):
        return await TopologyCollectionSnapshot.filter(
            id=run.snapshot_row_id,
            tenant_id=run.tenant_id,
            deleted=False,
        ).using_db(connection).first()

    async def _profile(self, run, connection):
        return await TopologyProfileRevision.filter(
            id=run.target_profile_revision_row_id,
            tenant_id=run.tenant_id,
            deleted=False,
        ).using_db(connection).first()

    async def _current_snapshot(self, topology, run, connection):
        row_id = getattr(topology, "current_snapshot_row_id", None)
        if row_id is None:
            return None
        return await TopologyCollectionSnapshot.filter(
            id=row_id, tenant_id=run.tenant_id, deleted=False
        ).using_db(connection).first()


def run_is_due(run: Any, now: datetime, *, publish_enabled: bool) -> bool:
    if _state(run) in _terminal_values() or run.cancellation_requested:
        return bool(run.cancellation_requested and _state(run) not in _terminal_values())
    if _lease_active(run, now):
        return False
    state = _state(run)
    if state == AnalysisRunState.RECEIVED.value:
        return run.next_retry_at is None or run.next_retry_at <= now
    if state == AnalysisRunState.RETRY_WAIT.value:
        return run.next_retry_at is not None and run.next_retry_at <= now
    if state == AnalysisRunState.RUNNING.value:
        return True
    return state == AnalysisRunState.WAITING_FOR_PUBLISH.value and publish_enabled


async def claim_due_run(
    worker_id: str,
    *,
    repository: LeaseRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
    token_factory: Callable[[], int] = lambda: secrets.randbelow(MAX_FENCING_TOKEN) + 1,
) -> ClaimedRun | None:
    repo = repository or OrmLeaseRepository()
    now = clock()
    async with repo.transaction() as connection:
        candidates = await repo.claim_candidates(now, CLAIM_SCAN_LIMIT, connection=connection)
        for run in candidates:
            claimed = await _claim_candidate(
                repo, run, worker_id, now, lease_ttl, token_factory, connection
            )
            if claimed is not None:
                return claimed
    return None


async def _claim_candidate(repo, run, worker_id, now, ttl, token_factory, connection):
    if not run_is_due(run, now, publish_enabled=settings.TOPOLOGY_V2_PUBLISH_ENABLED):
        return None
    terminal = await _terminalize_before_claim(repo, run, now, connection)
    if terminal or not await repo.payload_ready(run, connection=connection):
        return None
    context = await repo.get_guard_context(run, connection=connection)
    stale = _stale_reason(run, context)
    if stale is not None:
        await _finish_unclaimed(repo, run, stale, now, connection)
        return None
    token = _new_token(run.fencing_token, token_factory)
    _apply_claim(run, worker_id, now, ttl, token)
    await repo.save_run(run, _CLAIM_FIELDS, connection=connection)
    return ClaimedRun(run, _identity(run))


async def _terminalize_before_claim(repo, run, now, connection):
    if run.cancellation_requested:
        await _finish_unclaimed(repo, run, "CANCELLED_BY_REQUEST", now, connection)
        return True
    if int(run.attempt_count) >= int(run.max_attempts):
        code = run.error_code or "WORKER_LEASE_LOST"
        await _finish_unclaimed(repo, run, code, now, connection)
        return True
    return False


def _stale_reason(run: Any, context: GuardContext) -> str | None:
    topology, snapshot, profile = context.topology, context.snapshot, context.profile
    if topology is None or snapshot is None or profile is None:
        return "NEWER_COLLECTION_PUBLISHED"
    if str(snapshot.topology_row_id) != str(run.topology_row_id):
        return "NEWER_COLLECTION_PUBLISHED"
    if int(profile.revision) != int(snapshot.profile_revision):
        return "STALE_PROFILE"
    if int(topology.topology_profile_revision or 0) != int(profile.revision):
        return "STALE_PROFILE"
    if _older_collection(snapshot, topology, context.current_snapshot):
        return "NEWER_COLLECTION_PUBLISHED"
    if run.publish_as_current and _revision_changed(run, topology):
        return "PUBLICATION_CAS_MISMATCH"
    return None


def _older_collection(snapshot, topology, current_snapshot) -> bool:
    published = int(topology.last_published_collection_sequence or 0)
    if int(snapshot.collection_sequence) < published:
        return True
    return bool(
        current_snapshot is not None
        and int(current_snapshot.collection_sequence) > int(snapshot.collection_sequence)
    )


def _revision_changed(run, topology) -> bool:
    return (
        int(run.expected_current_graph_revision)
        != int(topology.current_graph_revision or 0)
        or int(run.expected_publication_revision)
        != int(topology.publication_revision or 0)
    )


async def heartbeat(
    identity: LeaseIdentity,
    *,
    repository: LeaseRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
) -> bool:
    now = clock()
    return await _fenced_update(
        identity,
        repository,
        lambda run: _apply_heartbeat(run, now, lease_ttl),
        ("heartbeat_at", "lease_expires_at"),
    )


async def advance_stage(
    identity: LeaseIdentity,
    stage: AnalysisRunStage,
    *,
    repository: LeaseRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    updates: Mapping[str, Any] | None = None,
) -> bool:
    del clock

    def mutate(run):
        require_stage_transition(run.stage, stage)
        for field, value in (updates or {}).items():
            setattr(run, field, value)
        run.stage = stage

    fields = tuple(updates or ()) + ("stage",)
    return await _fenced_update(identity, repository, mutate, fields)


async def schedule_retry(
    identity: LeaseIdentity,
    error_code: str,
    *,
    repository: LeaseRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    base_delay: timedelta = DEFAULT_RETRY_BASE,
    max_delay: timedelta = DEFAULT_RETRY_CAP,
) -> bool:
    now = clock()

    def mutate(run):
        if not classify_error(error_code).retryable or run.attempt_count >= run.max_attempts:
            _apply_terminal(run, AnalysisRunState.FAILED, error_code, now)
            return
        delay = exponential_backoff(run.attempt_count, base_delay, max_delay)
        _apply_retry(run, error_code, now + delay)

    return await _fenced_update(identity, repository, mutate, _RETRY_FIELDS)


async def wait_for_publish(
    identity: LeaseIdentity,
    *,
    repository: LeaseRepository | None = None,
) -> bool:
    def mutate(run):
        if _stage(run) != AnalysisRunStage.FACTS_READY.value:
            raise InvalidTransition("publish wait requires FACTS_READY")
        run.state = AnalysisRunState.WAITING_FOR_PUBLISH
        run.blocked_by = "PUBLISH_GATE"
        _clear_lease(run)

    return await _fenced_update(identity, repository, mutate, _WAIT_FIELDS)


async def publication_allowed(
    identity: LeaseIdentity,
    *,
    repository: LeaseRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> bool:
    repo = repository or OrmLeaseRepository()
    async with repo.transaction() as connection:
        run = await _get_locked(repo, identity.run_id, connection)
        if not _write_allowed(run, identity):
            return False
        if _stage(run) != AnalysisRunStage.PUBLISHING.value:
            raise InvalidTransition("publication requires PUBLISHING")
        context = await repo.get_guard_context(run, connection=connection)
        reason = _stale_reason(run, context)
        if reason is None:
            return True
        await _finish_unclaimed(repo, run, reason, clock(), connection)
        return False


async def mark_succeeded(
    identity: LeaseIdentity,
    *,
    repository: LeaseRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> bool:
    now = clock()

    def mutate(run):
        if _stage(run) != AnalysisRunStage.PUBLISHING.value:
            raise InvalidTransition("success requires PUBLISHING")
        _apply_terminal(run, AnalysisRunState.SUCCEEDED, None, now)

    return await _fenced_update(identity, repository, mutate, _TERMINAL_FIELDS)


async def release_lease(
    identity: LeaseIdentity,
    *,
    repository: LeaseRepository | None = None,
) -> bool:
    return await _fenced_update(identity, repository, _clear_lease, _LEASE_FIELDS)


async def _fenced_update(identity, repository, mutate, fields):
    repo = repository or OrmLeaseRepository()
    async with repo.transaction() as connection:
        run = await _get_locked(repo, identity.run_id, connection)
        if not _write_allowed(run, identity):
            return False
        mutate(run)
        await repo.save_run(run, fields, connection=connection)
        return True


async def _get_locked(repo, run_id, connection):
    getter = getattr(repo, "get_run_for_update", None)
    if getter is not None:
        return await getter(run_id, connection=connection)
    return next((run for run in repo.runs if str(run.id) == str(run_id)), None)


def stage_transition_allowed(current: Any, following: Any) -> bool:
    return STAGE_TRANSITIONS.get(_as_stage(current)) == _as_stage(following)


def require_stage_transition(current: Any, following: Any) -> None:
    if not stage_transition_allowed(current, following):
        raise InvalidTransition(f"illegal stage transition: {_stage_value(current)}")


def next_stage(stage: Any) -> AnalysisRunStage:
    current = _as_stage(stage)
    if current not in STAGE_TRANSITIONS:
        raise InvalidTransition(f"no stage follows {_stage_value(stage)}")
    return STAGE_TRANSITIONS[current]


def exponential_backoff(
    attempt_count: int,
    base_delay: timedelta = DEFAULT_RETRY_BASE,
    max_delay: timedelta = DEFAULT_RETRY_CAP,
) -> timedelta:
    multiplier = 2 ** max(int(attempt_count) - 1, 0)
    return min(base_delay * multiplier, max_delay)


def _apply_claim(run, worker_id, now, ttl, token):
    run.state = AnalysisRunState.RUNNING
    run.blocked_by = None
    run.attempt_count += 1
    run.lease_generation += 1
    run.fencing_token = token
    run.lease_owner = worker_id
    run.heartbeat_at = now
    run.lease_expires_at = now + ttl
    run.next_retry_at = None
    run.started_at = run.started_at or now


def _apply_heartbeat(run, now, ttl):
    run.heartbeat_at = now
    run.lease_expires_at = now + ttl


def _apply_retry(run, code, retry_at):
    run.state = AnalysisRunState.RETRY_WAIT
    run.error_code = code
    run.error_summary = _PUBLIC_SUMMARIES[code]
    run.retryable = True
    run.next_retry_at = retry_at
    run.blocked_by = None
    _clear_lease(run)


def _apply_terminal(run, state, code, now):
    run.state = state
    run.error_code = code
    run.error_summary = _PUBLIC_SUMMARIES.get(code) if code else None
    run.retryable = False
    run.next_retry_at = None
    run.blocked_by = None
    run.finished_at = now
    _clear_lease(run)


async def _finish_unclaimed(repo, run, code, now, connection):
    state = (
        AnalysisRunState.CANCELLED
        if code == "CANCELLED_BY_REQUEST"
        else AnalysisRunState.SUPERSEDED
        if code in {"NEWER_COLLECTION_PUBLISHED", "STALE_PROFILE", "PUBLICATION_CAS_MISMATCH"}
        else AnalysisRunState.FAILED
    )
    _apply_terminal(run, state, code, now)
    await repo.save_run(run, _TERMINAL_FIELDS, connection=connection)


def _clear_lease(run):
    run.lease_owner = None
    run.lease_expires_at = None
    run.heartbeat_at = None


def _identity(run):
    return LeaseIdentity(
        str(run.id), run.lease_owner, int(run.lease_generation), int(run.fencing_token)
    )


def _new_token(old_token, factory):
    token = int(factory())
    if token < 1 or token > MAX_FENCING_TOKEN:
        raise ValueError("fencing token must fit a signed BigInt")
    return token if token != int(old_token or 0) else (token % MAX_FENCING_TOKEN) + 1


def _lease_active(run, now):
    return run.lease_expires_at is not None and run.lease_expires_at > now


def _due_query(now, publish_enabled):
    lease_due = Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now)
    received = Q(state=AnalysisRunState.RECEIVED) & (
        Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)
    )
    retry = Q(
        state=AnalysisRunState.RETRY_WAIT,
        next_retry_at__isnull=False,
        next_retry_at__lte=now,
    )
    due = lease_due & (received | retry | Q(state=AnalysisRunState.RUNNING))
    if publish_enabled:
        due |= lease_due & Q(state=AnalysisRunState.WAITING_FOR_PUBLISH)
    return due


def _write_allowed(run, identity):
    return bool(
        run is not None
        and worker_write_allowed(
            run, identity.lease_generation, identity.fencing_token
        )
    )


def _state(run):
    return _enum_value(run.state)


def _stage(run):
    return _enum_value(run.stage)


def _as_stage(value):
    return value if isinstance(value, AnalysisRunStage) else AnalysisRunStage(value)


def _stage_value(value):
    return _enum_value(value)


def _enum_value(value):
    return getattr(value, "value", value)


def _terminal_values():
    return {_enum_value(item) for item in TERMINAL_STATES}


_LEASE_FIELDS = ("lease_owner", "lease_expires_at", "heartbeat_at")
_CLAIM_FIELDS = (
    "state", "blocked_by", "attempt_count", "lease_generation", "fencing_token",
    "lease_owner", "heartbeat_at", "lease_expires_at", "next_retry_at", "started_at",
)
_RETRY_FIELDS = (
    "state", "error_code", "error_summary", "retryable", "next_retry_at",
    "blocked_by", *_LEASE_FIELDS, "finished_at",
)
_WAIT_FIELDS = ("state", "blocked_by", *_LEASE_FIELDS)
_TERMINAL_FIELDS = (
    "state", "error_code", "error_summary", "retryable", "next_retry_at",
    "blocked_by", "finished_at", *_LEASE_FIELDS,
)


__all__ = [
    "ClaimedRun", "GuardContext", "InvalidTransition", "LeaseIdentity",
    "OrmLeaseRepository", "STAGE_ORDER", "STAGE_TRANSITIONS", "advance_stage",
    "claim_due_run", "exponential_backoff", "heartbeat", "mark_succeeded",
    "next_stage", "publication_allowed", "release_lease", "require_stage_transition", "run_is_due",
    "schedule_retry", "stage_transition_allowed", "wait_for_publish",
]
