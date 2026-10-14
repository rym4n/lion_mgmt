"""Durable status, reanalysis, cancellation, and worker-fencing rules."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

import orjson
from tortoise.exceptions import ValidationError
from tortoise.transactions import in_transaction

from models._network_topology_v2_analysis import TopologyAnalysisRun
from models._network_topology_v2_base import (
    AnalysisKind,
    AnalysisRunStage,
    AnalysisRunState,
)
from models._network_topology_v2_collection import (
    TopologyCollectionSnapshot,
    TopologyProfileRevision,
)
from models.network_topology import NetworkTopology
from services.network_topology import contracts

COLLECTION_V2_SCHEMA = "aioe.network-topology.collection/v2"
FACTS_V1_SCHEMA = "aioe.network-topology.facts/v1"
GRAPH_V2_SCHEMA = "aioe.network-topology.graph/v2"
STATUS_URL = "/api/network-topology/config-sync/status"
STATUS_REQUEST_SCHEMA = "aioe.network-topology.api/status-request/v1"
REANALYZE_REQUEST_SCHEMA = "aioe.network-topology.api/reanalyze-request/v1"
CANCEL_REQUEST_SCHEMA = "aioe.network-topology.api/cancel-request/v1"

RUN_INVALID_REQUEST = "SCHEMA_INVALID"
RUN_NOT_FOUND = "SCHEMA_INVALID"
RUN_SNAPSHOT_NOT_FOUND = "SCHEMA_INVALID"
RUN_SNAPSHOT_SCHEMA_INVALID = "SCHEMA_INVALID"
RUN_PROFILE_NOT_FOUND = "SCHEMA_INVALID"
RUN_CAS_MISMATCH = "UPLOAD_CONFLICT"
RUN_PUBLISH_COMMIT_STARTED = "UPLOAD_CONFLICT"
RUN_CREATE_FAILED = "WORKER_UNAVAILABLE"

TERMINAL_STATES = frozenset(
    {
        AnalysisRunState.SUCCEEDED,
        AnalysisRunState.FAILED,
        AnalysisRunState.SUPERSEDED,
        AnalysisRunState.CANCELLED,
    }
)
TEMPORARY_ERRORS = frozenset(
    {
        "PROVIDER_TIMEOUT",
        "TEMPORARY_NETWORK_FAILURE",
        "TEMPORARY_DATABASE_FAILURE",
        "WORKER_LEASE_LOST",
        "AGENT_TIMEOUT",
        "AGENT_SUBMIT_REJECTED",
        "AGENT_VALIDATION_EXHAUSTED",
        "AGENT_UNAVAILABLE",
    }
)
PUBLIC_RUN_ERRORS = TEMPORARY_ERRORS | frozenset(
    {
        "WORKER_UNAVAILABLE",
        "AGENT_NOT_CONFIGURED",
        "AGENT_MODEL_NOT_CONFIGURED",
        "AGENT_INPUT_UNAVAILABLE",
        "RUN_SUPERSEDED",
        "SCHEMA_INVALID",
        "PARSER_FAILED",
        "EVIDENCE_INVALID",
        "GRAPH_VALIDATION_FAILED",
        "NEWER_COLLECTION_PUBLISHED",
        "PUBLICATION_CAS_MISMATCH",
        "STALE_PROFILE",
        "UPGRADE_INTENT_INVALID",
        "CANCELLED_BY_REQUEST",
    }
)
_PUBLIC_SUMMARIES = {
    "AGENT_INPUT_UNAVAILABLE": "Analysis agent input is unavailable",
    "AGENT_TIMEOUT": "Analysis agent timed out",
    "AGENT_SUBMIT_REJECTED": "Analysis agent submission was not accepted",
    "AGENT_VALIDATION_EXHAUSTED": "Analysis agent validation attempts exhausted",
    "AGENT_UNAVAILABLE": "Analysis agent is temporarily unavailable",
    "AGENT_MODEL_NOT_CONFIGURED": "Analysis agent model is not configured",
    "AGENT_NOT_CONFIGURED": "Analysis agent is not configured",
    "RUN_SUPERSEDED": "Analysis run was superseded",
    "WORKER_UNAVAILABLE": "Analysis worker is temporarily unavailable",
    "PROVIDER_TIMEOUT": "Analysis provider timed out",
    "TEMPORARY_NETWORK_FAILURE": "Temporary network failure",
    "TEMPORARY_DATABASE_FAILURE": "Temporary database failure",
    "WORKER_LEASE_LOST": "Analysis worker lease was lost",
    "SCHEMA_INVALID": "Snapshot schema validation failed",
    "PARSER_FAILED": "Snapshot parsing failed",
    "EVIDENCE_INVALID": "Evidence validation failed",
    "GRAPH_VALIDATION_FAILED": "Graph validation failed",
    "NEWER_COLLECTION_PUBLISHED": "A newer collection was published",
    "PUBLICATION_CAS_MISMATCH": "Publication revision changed",
    "STALE_PROFILE": "Topology profile changed",
    "UPGRADE_INTENT_INVALID": "Topology upgrade intent is no longer valid",
    "CANCELLED_BY_REQUEST": "Analysis was cancelled by request",
}
_API_ERRORS = {
    "SCHEMA_INVALID": (400, "分析请求不合法", False),
    "UPLOAD_CONFLICT": (409, "分析目标状态已更新", False),
    "WORKER_UNAVAILABLE": (503, "分析服务暂时不可用", True),
}


@dataclass(frozen=True, slots=True)
class ErrorClassification:
    retryable: bool
    terminal: bool


class AnalysisRunError(RuntimeError):
    """Stable public failure that never includes persistence details."""

    def __init__(self, code: str, status: int | None = None) -> None:
        default_status, message, retryable = _API_ERRORS[code]
        self.code = code
        self.status = default_status if status is None else status
        self.public_message = message
        self.retryable = retryable
        super().__init__(code)


class AnalysisRunRepository(Protocol):
    def transaction(self): ...

    async def get_run(self, tenant_id, sync_id, *, connection=None, lock=False): ...

    async def get_snapshot(
        self, tenant_id, topology_id, snapshot_id, *, connection
    ): ...

    async def get_topology(
        self, tenant_id, topology_id, *, connection, lock
    ): ...

    async def get_profile(
        self, tenant_id, topology_id, revision, *, connection, lock=False
    ): ...

    async def create_run(self, values, *, connection): ...

    async def save_run(self, run, fields, *, connection): ...


class OrmAnalysisRunRepository:
    @asynccontextmanager
    async def transaction(self):
        async with in_transaction() as connection:
            yield connection

    async def get_run(self, tenant_id, sync_id, *, connection=None, lock=False):
        query = TopologyAnalysisRun.filter(
            id=sync_id, tenant_id=tenant_id, deleted=False
        )
        return await _first(query, connection=connection, lock=lock)

    async def get_snapshot(self, tenant_id, topology_id, snapshot_id, *, connection):
        query = TopologyCollectionSnapshot.filter(
            tenant_id=tenant_id,
            topology_row_id=topology_id,
            snapshot_id=snapshot_id,
            deleted=False,
        )
        return await _first(query, connection=connection, lock=True)

    async def get_topology(self, tenant_id, topology_id, *, connection, lock):
        query = NetworkTopology.filter(
            id=topology_id, tenant_id=tenant_id, deleted=False
        )
        return await _first(query, connection=connection, lock=lock)

    async def get_profile(
        self, tenant_id, topology_id, revision, *, connection, lock=False
    ):
        query = TopologyProfileRevision.filter(
            tenant_id=tenant_id,
            topology_row_id=topology_id,
            revision=revision,
            deleted=False,
        )
        return await _first(query, connection=connection, lock=lock)

    async def create_run(self, values, *, connection):
        return await TopologyAnalysisRun.create(using_db=connection, **values)

    async def save_run(self, run, fields, *, connection):
        await run.save(using_db=connection, update_fields=[*fields, "utime"])


async def _first(query, *, connection=None, lock=False):
    try:
        if connection is not None:
            query = query.using_db(connection)
        if lock:
            query = query.select_for_update()
        return await query.first()
    except ValidationError:
        return None


def classify_error(error_code: str | None) -> ErrorClassification:
    retryable = error_code in TEMPORARY_ERRORS
    return ErrorClassification(retryable=retryable, terminal=not retryable)


def worker_write_allowed(
    run: Any, lease_generation: int, fencing_token: int
) -> bool:
    return bool(
        _enum_value(run.state) not in {_enum_value(item) for item in TERMINAL_STATES}
        and not run.cancellation_requested
        and run.lease_generation == lease_generation
        and run.fencing_token == fencing_token
    )


def parse_status_request(payload: object) -> dict[str, Any]:
    return _validated_request(payload, STATUS_REQUEST_SCHEMA)


def parse_reanalyze_request(payload: object) -> dict[str, Any]:
    return _validated_request(payload, REANALYZE_REQUEST_SCHEMA)


def parse_cancel_request(payload: object) -> dict[str, Any]:
    return _validated_request(payload, CANCEL_REQUEST_SCHEMA)


def _validated_request(payload: object, schema: str) -> dict[str, Any]:
    try:
        contracts.validate_contract(payload, schema)
    except contracts.ContractError:
        raise AnalysisRunError(RUN_INVALID_REQUEST) from None
    return dict(payload)


async def get_status(
    tenant_id: str,
    sync_id: str,
    *,
    repository: AnalysisRunRepository | None = None,
) -> dict[str, Any]:
    _require_text(tenant_id)
    _require_text(sync_id)
    repo = repository or OrmAnalysisRunRepository()
    try:
        run = await repo.get_run(tenant_id, sync_id)
        if run is None:
            raise AnalysisRunError(RUN_NOT_FOUND, status=404)
        topology = await repo.get_topology(
            tenant_id, run.topology_row_id, connection=None, lock=False
        )
        if topology is None:
            raise AnalysisRunError(RUN_NOT_FOUND, status=404)
        return _status_payload(run, topology)
    except AnalysisRunError:
        raise
    except Exception:
        raise AnalysisRunError(RUN_CREATE_FAILED) from None


async def reanalyze_snapshot(
    tenant_id: str,
    snapshot_id: str,
    *,
    topology_id: str,
    target_profile_revision: int,
    publish_as_current: bool,
    expected_current_graph_revision: int,
    expected_publication_revision: int,
    actor: str,
    repository: AnalysisRunRepository | None = None,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> dict[str, Any]:
    _validate_reanalysis_input(
        tenant_id,
        snapshot_id,
        topology_id,
        target_profile_revision,
        publish_as_current,
        expected_current_graph_revision,
        expected_publication_revision,
        actor,
    )
    repo = repository or OrmAnalysisRunRepository()
    try:
        run = await _create_reanalysis(
            repo,
            tenant_id,
            snapshot_id,
            topology_id,
            target_profile_revision,
            publish_as_current,
            expected_current_graph_revision,
            expected_publication_revision,
            actor,
            id_factory,
        )
    except AnalysisRunError:
        raise
    except Exception:
        raise AnalysisRunError(RUN_CREATE_FAILED) from None
    return _accepted_payload(run, snapshot_id)


async def _create_reanalysis(
    repo,
    tenant_id,
    snapshot_id,
    topology_id,
    profile_revision,
    publish_as_current,
    expected_graph_revision,
    expected_publication_revision,
    actor,
    id_factory,
):
    async with repo.transaction() as connection:
        topology = await repo.get_topology(
            tenant_id, topology_id, connection=connection, lock=True
        )
        _require_topology_cas(
            topology, expected_graph_revision, expected_publication_revision
        )
        snapshot = await repo.get_snapshot(
            tenant_id, topology_id, snapshot_id, connection=connection
        )
        _require_snapshot(snapshot, topology_id)
        profile = await repo.get_profile(
            tenant_id,
            topology_id,
            profile_revision,
            connection=connection,
            lock=True,
        )
        _require_profile(profile, topology, publish_as_current)
        values = _new_run_values(
            tenant_id, snapshot, profile, topology, publish_as_current, actor, id_factory
        )
        return await repo.create_run(values, connection=connection)


async def cancel_analysis(
    tenant_id: str,
    sync_id: str,
    *,
    actor: str,
    reason: str,
    repository: AnalysisRunRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    _require_text(tenant_id)
    _require_text(sync_id)
    _validate_cancellation(actor, reason)
    repo = repository or OrmAnalysisRunRepository()
    try:
        async with repo.transaction() as connection:
            run = await repo.get_run(
                tenant_id, sync_id, connection=connection, lock=True
            )
            if run is None:
                raise AnalysisRunError(RUN_NOT_FOUND, status=404)
            if _is_terminal(run.state):
                return _cancel_payload(run)
            if _enum_value(run.stage) == AnalysisRunStage.PUBLISHING.value:
                raise AnalysisRunError(RUN_PUBLISH_COMMIT_STARTED)
            fields = _apply_cancellation(run, actor, reason, clock())
            await repo.save_run(run, fields, connection=connection)
            return _cancel_payload(run)
    except AnalysisRunError:
        raise
    except Exception:
        raise AnalysisRunError(RUN_CREATE_FAILED) from None


def _apply_cancellation(run, actor, reason, timestamp) -> tuple[str, ...]:
    run.state = AnalysisRunState.CANCELLED
    run.error_code = "CANCELLED_BY_REQUEST"
    run.error_summary = _PUBLIC_SUMMARIES[run.error_code]
    run.retryable = False
    run.next_retry_at = None
    run.blocked_by = None
    run.finished_at = timestamp
    run.cancellation_requested = True
    run.cancellation_requested_by = actor
    run.cancellation_requested_at = timestamp
    run.cancellation_reason = reason
    run.lease_generation += 1
    run.fencing_token += 1
    run.lease_owner = None
    run.lease_expires_at = None
    run.heartbeat_at = None
    return (
        "state", "error_code", "error_summary", "retryable", "next_retry_at",
        "blocked_by", "finished_at", "cancellation_requested",
        "cancellation_requested_by", "cancellation_requested_at",
        "cancellation_reason", "lease_generation", "fencing_token", "lease_owner",
        "lease_expires_at", "heartbeat_at",
    )


def _status_payload(run, topology) -> dict[str, Any]:
    state = _enum_value(run.state)
    error_code, error_summary = _public_status_error(run, state)
    terminal = _is_terminal(state)
    return {
        "sync_id": str(run.id),
        "state": state,
        "terminal": terminal,
        "stage": _enum_value(run.stage),
        "blocked_by": _status_blocked_by(run, state),
        "attempt": int(run.attempt_count),
        "retryable": _status_retryable(run, state),
        "next_retry_at": _status_next_retry(run, state),
        "published": state == AnalysisRunState.SUCCEEDED.value,
        "graph_revision": int(topology.current_graph_revision or 0),
        "publication_revision": int(topology.publication_revision or 0),
        "error_code": error_code,
        "error_summary": error_summary,
    }


def _status_retryable(run, state: str) -> bool:
    if state in {AnalysisRunState.RECEIVED.value, AnalysisRunState.RUNNING.value}:
        return True
    if state == AnalysisRunState.RETRY_WAIT.value:
        return bool(run.retryable and classify_error(run.error_code).retryable)
    return False


def _status_next_retry(run, state: str) -> str | None:
    if state != AnalysisRunState.RETRY_WAIT.value or run.next_retry_at is None:
        return None
    return _rfc3339(run.next_retry_at)


def _status_blocked_by(run, state: str) -> str | None:
    if state == AnalysisRunState.WAITING_FOR_PUBLISH.value:
        return "PUBLISH_GATE"
    if state == AnalysisRunState.RECEIVED.value and run.blocked_by == "ANALYSIS_GATE":
        return "ANALYSIS_GATE"
    return None


def _public_status_error(run, state: str) -> tuple[str | None, str | None]:
    if state == AnalysisRunState.CANCELLED.value:
        code = "CANCELLED_BY_REQUEST"
        return code, _PUBLIC_SUMMARIES[code]
    no_error = {
        AnalysisRunState.RECEIVED.value,
        AnalysisRunState.RUNNING.value,
        AnalysisRunState.WAITING_FOR_PUBLISH.value,
        AnalysisRunState.SUCCEEDED.value,
    }
    worker_unavailable = (
        state == AnalysisRunState.RECEIVED.value
        and run.error_code == "WORKER_UNAVAILABLE"
    )
    if state in no_error and not worker_unavailable:
        return None, None
    code = run.error_code if run.error_code in PUBLIC_RUN_ERRORS else "SCHEMA_INVALID"
    return code, _PUBLIC_SUMMARIES[code]


def _new_run_values(
    tenant_id, snapshot, profile, topology, publish_as_current, actor, id_factory
) -> dict[str, Any]:
    parser_registry = contracts.load_registry(contracts.PARSER_REGISTRY_URI)
    return {
        "tenant_id": tenant_id,
        "topology_row_id": topology.id,
        "snapshot_row_id": snapshot.id,
        "target_profile_revision_row_id": profile.id,
        "submitter_subject": actor,
        "playbook_id": snapshot.playbook_id,
        "run_id": snapshot.run_id,
        "analysis_kind": AnalysisKind.REANALYZE,
        "publication_attempt_id": _require_text(id_factory()),
        "publish_as_current": publish_as_current,
        "expected_publication_revision": topology.publication_revision or 0,
        "expected_current_graph_revision": topology.current_graph_revision or 0,
        "parser_bundle_version": contracts.PARSER_BUNDLE_ID,
        "input_schema_version": COLLECTION_V2_SCHEMA,
        "facts_schema_version": FACTS_V1_SCHEMA,
        "graph_schema_version": GRAPH_V2_SCHEMA,
        "redaction_version": contracts.REDACTION_VERSION,
        "deterministic_parser_versions": orjson.dumps(
            parser_registry["deterministic_parsers"]
        ).decode(),
    }


def _require_topology_cas(topology, graph_revision, publication_revision) -> None:
    if topology is None:
        raise AnalysisRunError(RUN_NOT_FOUND, status=404)
    current = (
        int(topology.current_graph_revision or 0),
        int(topology.publication_revision or 0),
    )
    if current != (graph_revision, publication_revision):
        raise AnalysisRunError(RUN_CAS_MISMATCH)


def _require_snapshot(snapshot, topology_id) -> None:
    if snapshot is None or str(snapshot.topology_row_id) != str(topology_id):
        raise AnalysisRunError(RUN_SNAPSHOT_NOT_FOUND, status=404)
    if snapshot.schema_version != COLLECTION_V2_SCHEMA:
        raise AnalysisRunError(RUN_SNAPSHOT_SCHEMA_INVALID, status=422)


def _require_profile(profile, topology, publish_as_current) -> None:
    if profile is None:
        raise AnalysisRunError(RUN_PROFILE_NOT_FOUND, status=404)
    if publish_as_current and profile.revision != topology.topology_profile_revision:
        raise AnalysisRunError(RUN_CAS_MISMATCH)


def _validate_reanalysis_input(*values) -> None:
    tenant_id, snapshot_id, topology_id, profile, publish, graph, publication, actor = values
    for value in (tenant_id, snapshot_id, topology_id, actor):
        _require_text(value)
    if type(profile) is not int or profile < 1:
        raise AnalysisRunError(RUN_INVALID_REQUEST)
    if type(publish) is not bool:
        raise AnalysisRunError(RUN_INVALID_REQUEST)
    for revision in (graph, publication):
        if type(revision) is not int or revision < 0:
            raise AnalysisRunError(RUN_INVALID_REQUEST)


def _validate_cancellation(actor: str, reason: str) -> None:
    _require_text(actor)
    text = _require_text(reason)
    if len(text) > 512:
        raise AnalysisRunError(RUN_INVALID_REQUEST)


def _accepted_payload(run, snapshot_id) -> dict[str, Any]:
    return {
        "sync_id": str(run.id),
        "snapshot_id": snapshot_id,
        "status": AnalysisRunState.RECEIVED.value,
        "status_url": STATUS_URL,
    }


def _cancel_payload(run) -> dict[str, Any]:
    return {
        "sync_id": str(run.id),
        "state": _enum_value(run.state),
        "cancellation_requested": True,
    }


def _require_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisRunError(RUN_INVALID_REQUEST)
    return value.strip()


def _is_terminal(state: Any) -> bool:
    return _enum_value(state) in {_enum_value(item) for item in TERMINAL_STATES}


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AnalysisRunError(RUN_CREATE_FAILED)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "AnalysisRunError",
    "COLLECTION_V2_SCHEMA",
    "RUN_CAS_MISMATCH",
    "RUN_NOT_FOUND",
    "RUN_PUBLISH_COMMIT_STARTED",
    "RUN_SNAPSHOT_SCHEMA_INVALID",
    "cancel_analysis",
    "classify_error",
    "get_status",
    "parse_cancel_request",
    "parse_reanalyze_request",
    "parse_status_request",
    "reanalyze_snapshot",
    "worker_write_allowed",
]
