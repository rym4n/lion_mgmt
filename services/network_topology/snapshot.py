"""Admission and atomic commit for direct topology collection snapshots."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

import orjson
from sanic import json as sanic_json
from tortoise.transactions import in_transaction

import settings
from models import NetworkTopology
from models._network_topology_v2_analysis import TopologyAnalysisRun
from models._network_topology_v2_base import AnalysisKind, ManifestStatus
from models._network_topology_v2_collection import (
    TopologyCollectionManifest,
    TopologyCollectionSnapshot,
    TopologyProfileRevision,
)
from services.network_topology import analysis_runs, contracts
from services.network_topology.canonicalization import (
    CanonicalizationError,
    canonicalize_and_hash_manifest,
    canonicalize_json,
)
from services.network_topology.payload_store import (
    PayloadStore,
    PreparedEnvelope,
    PreparedPayload,
    prepare_stored_envelope,
)


COLLECTION_SCHEMA = "aioe.network-topology.collection/v2"
REQUEST_SCHEMA = "aioe.network-topology.api/config-sync-request/v2"
RESPONSE_SCHEMA = "aioe.network-topology.api/config-sync-response/v2"
DEFAULT_DIRECT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_COLLECTION_SPAN_SECONDS = 3600
DEFAULT_MAX_OBSERVATION_AGE_SECONDS = 3600
SNAPSHOT_RETENTION = timedelta(days=90)

INVALID_REQUEST = "INVALID_REQUEST"
PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
SCHEMA_INVALID = "SCHEMA_INVALID"
MANIFEST_INVALID = "MANIFEST_INVALID"
MANIFEST_CONFLICT = "MANIFEST_CONFLICT"
SNAPSHOT_CONFLICT = "SNAPSHOT_CONFLICT"
COMPLETENESS_INVALID = "COMPLETENESS_INVALID"
COLLECTION_WINDOW_INVALID = "COLLECTION_WINDOW_INVALID"
PAYLOAD_HASH_INVALID = "PAYLOAD_HASH_INVALID"
INGRESS_DISABLED = "INGRESS_DISABLED"
SYNC_UNAVAILABLE = "SYNC_UNAVAILABLE"

_ERRORS = {
    INVALID_REQUEST: (400, "快照同步请求不合法"),
    PAYLOAD_TOO_LARGE: (413, "快照同步请求超过大小限制"),
    SCHEMA_INVALID: (400, "快照 Schema 不合法"),
    MANIFEST_INVALID: (409, "Manifest 绑定无效"),
    MANIFEST_CONFLICT: (409, "Manifest 已绑定其他快照"),
    SNAPSHOT_CONFLICT: (409, "snapshot_id 已绑定其他内容"),
    COMPLETENESS_INVALID: (422, "快照完整性声明不成立"),
    COLLECTION_WINDOW_INVALID: (422, "快照采集窗口无效"),
    PAYLOAD_HASH_INVALID: (422, "快照内容哈希校验失败"),
    INGRESS_DISABLED: (503, "v2 拓扑同步入口未启用"),
    SYNC_UNAVAILABLE: (503, "快照同步服务暂时不可用"),
}


class SnapshotError(RuntimeError):
    """Stable public error which never contains submitted payload data."""

    def __init__(self, code: str):
        self.code = code
        self.status, self.public_message = _ERRORS[code]
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class ValidatedSnapshot:
    topology_id: str
    snapshot_id: str
    envelope: dict[str, Any]
    prepared_envelope: PreparedEnvelope
    started_at: datetime
    ended_at: datetime

    @property
    def stored_envelope(self) -> dict[str, Any]:
        return self.prepared_envelope.normalized

    @property
    def payload_hash(self) -> str:
        return self.prepared_envelope.payload_hash


@dataclass(frozen=True, slots=True)
class SyncAccepted:
    sync_id: str
    snapshot_id: str
    status: str = "RECEIVED"
    status_url: str = analysis_runs.STATUS_URL

    def as_dict(self) -> dict[str, str]:
        return {
            "sync_id": self.sync_id,
            "snapshot_id": self.snapshot_id,
            "status": self.status,
            "status_url": self.status_url,
        }


class SnapshotRepository(Protocol):
    def transaction(self): ...

    async def get_manifest(self, tenant_id, topology_id, manifest_id): ...

    async def get_profile(self, tenant_id, topology_id, profile_id): ...

    async def lock_manifest(self, tenant_id, topology_id, manifest_id, connection): ...

    async def get_snapshot(self, tenant_id, topology_id, snapshot_id, connection): ...

    async def create_snapshot(self, values, connection): ...

    async def create_analysis_run(self, values, connection): ...

    async def save_manifest(self, manifest, connection): ...


class OrmSnapshotRepository:
    @asynccontextmanager
    async def transaction(self):
        async with in_transaction() as connection:
            yield connection

    async def get_manifest(self, tenant_id, topology_id, manifest_id):
        return await TopologyCollectionManifest.filter(
            tenant_id=tenant_id,
            topology_row_id=topology_id,
            manifest_id=manifest_id,
            deleted=False,
        ).first()

    async def get_profile(self, tenant_id, topology_id, profile_id):
        return await TopologyProfileRevision.filter(
            id=profile_id,
            tenant_id=tenant_id,
            topology_row_id=topology_id,
            deleted=False,
        ).first()

    async def lock_manifest(self, tenant_id, topology_id, manifest_id, connection):
        await _lock_topology(tenant_id, topology_id, connection)
        query = TopologyCollectionManifest.filter(
            tenant_id=tenant_id,
            topology_row_id=topology_id,
            manifest_id=manifest_id,
            deleted=False,
        )
        return await query.using_db(connection).select_for_update().first()

    async def lock_collection_run(self, tenant_id, topology_id, actor, connection):
        if not actor.startswith("collection-run:"):
            return None
        from models.network_topology_v2 import TopologyCollectionRun
        row = await TopologyCollectionRun.filter(
            id=actor.removeprefix("collection-run:"), tenant_id=tenant_id,
            topology_id=topology_id, deleted=False,
        ).using_db(connection).select_for_update().first()
        if row is None or row.state != "COLLECTED":
            raise SnapshotError(MANIFEST_INVALID)
        return row

    async def bind_collection_analysis(self, collection, analysis, snapshot_id, connection):
        if collection is None:
            return
        collection.sync_id = collection.analysis_run_id = str(analysis.id)
        collection.snapshot_id = snapshot_id
        collection.state = "SUBMITTED"
        collection.snapshot_draft = None
        await collection.save(using_db=connection, update_fields=[
            "sync_id", "analysis_run_id", "snapshot_id", "state", "snapshot_draft",
        ])

    async def get_snapshot(self, tenant_id, topology_id, snapshot_id, connection):
        query = TopologyCollectionSnapshot.filter(
            tenant_id=tenant_id,
            topology_row_id=topology_id,
            snapshot_id=snapshot_id,
            deleted=False,
        )
        return await query.using_db(connection).select_for_update().first()

    async def create_snapshot(self, values, connection):
        return await TopologyCollectionSnapshot.create(using_db=connection, **values)

    async def create_analysis_run(self, values, connection):
        return await TopologyAnalysisRun.create(using_db=connection, **values)

    async def save_manifest(self, manifest, connection):
        fields = (
            "status",
            "consumed_at",
            "committed_snapshot_row_id",
            "committed_snapshot_id",
            "committed_payload_hash",
            "committed_analysis_run_row_id",
            "utime",
        )
        await manifest.save(using_db=connection, update_fields=fields)


async def _lock_topology(tenant_id, topology_id, connection) -> None:
    row = await (
        NetworkTopology.filter(id=topology_id, tenant_id=tenant_id, deleted=False)
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if row is None:
        raise SnapshotError(MANIFEST_INVALID)


def validate_direct_snapshot(
    topology_id: str,
    raw_envelope: object,
    *,
    body_size: int,
    now: datetime | None = None,
) -> ValidatedSnapshot:
    """Validate direct admission in the mandated order and redact immediately."""
    _validate_size(body_size)
    envelope = _validate_schema(topology_id, raw_envelope)
    _validate_manifest_projection(envelope)
    _validate_completeness(envelope)
    started, ended = _validate_collection_window(envelope, now)
    prepared = _prepare_redacted(envelope)
    return ValidatedSnapshot(
        topology_id,
        envelope["snapshot_id"],
        prepared.normalized,
        prepared,
        started,
        ended,
    )


def _validate_size(body_size: int) -> None:
    maximum = getattr(
        settings, "TOPOLOGY_V2_DIRECT_MAX_BYTES", DEFAULT_DIRECT_MAX_BYTES
    )
    if type(body_size) is not int or body_size < 0:
        raise SnapshotError(INVALID_REQUEST)
    if type(maximum) is not int or maximum < 1 or body_size > maximum:
        raise SnapshotError(PAYLOAD_TOO_LARGE)


def _validate_schema(topology_id: str, raw_envelope: object) -> dict[str, Any]:
    if not isinstance(topology_id, str) or not topology_id.strip():
        raise SnapshotError(INVALID_REQUEST)
    try:
        contracts.validate_collection_admission(raw_envelope)
    except contracts.ContractError:
        raise SnapshotError(SCHEMA_INVALID) from None
    return dict(raw_envelope)


def _validate_manifest_projection(envelope: dict[str, Any]) -> None:
    manifest = envelope["target_manifest"]
    try:
        canonical = canonicalize_and_hash_manifest(manifest)
    except CanonicalizationError:
        raise SnapshotError(MANIFEST_INVALID) from None
    if canonical.manifest_hash != manifest["manifest_hash"]:
        raise SnapshotError(MANIFEST_INVALID)
    collector = envelope["collector"]
    if not collector["playbook_id"] or not collector["run_id"]:
        raise SnapshotError(MANIFEST_INVALID)


def _validate_completeness(envelope: dict[str, Any]) -> None:
    manifest = envelope["target_manifest"]
    completeness = envelope["completeness"]
    expected = {item["resource_id"] for item in manifest["expected_resources"]}
    collected, observations = _collected_observations(envelope)
    declared = set(completeness["collected_resource_ids"])
    if set(completeness["expected_resource_ids"]) != expected:
        raise SnapshotError(COMPLETENESS_INVALID)
    if declared != expected or collected.keys() != expected:
        raise SnapshotError(COMPLETENESS_INVALID)
    _validate_coverage(manifest, completeness, observations)


def _collected_observations(envelope) -> tuple[dict[str, int], dict[str, dict]]:
    counts: dict[str, int] = {}
    observations: dict[str, dict[str, Any]] = {}
    for source in envelope["sources"]:
        for resource in source["resources"]:
            resource_id = resource["resource_id"]
            counts[resource_id] = counts.get(resource_id, 0) + 1
            if counts[resource_id] > 1:
                raise SnapshotError(COMPLETENESS_INVALID)
            observations[resource_id] = {
                item["kind"]: item for item in resource["result"]["observations"]
            }
    return counts, observations


def _validate_coverage(manifest, completeness, observations) -> None:
    coverage = completeness["observation_coverage"]
    by_resource = {item["resource_id"]: item for item in coverage}
    if len(by_resource) != len(coverage) or set(by_resource) != set(observations):
        raise SnapshotError(COMPLETENESS_INVALID)
    for expected in manifest["expected_resources"]:
        resource_id = expected["resource_id"]
        _validate_resource_coverage(
            expected, by_resource[resource_id], observations[resource_id]
        )


def _validate_resource_coverage(expected, coverage, observations) -> None:
    statuses = coverage["kind_status"]
    observed_kinds = set(observations)
    if set(coverage["collected_kinds"]) != observed_kinds:
        raise SnapshotError(COMPLETENESS_INVALID)
    if set(statuses) != observed_kinds:
        raise SnapshotError(COMPLETENESS_INVALID)
    required = set(expected["required_kinds"])
    if not required <= observed_kinds:
        raise SnapshotError(COMPLETENESS_INVALID)
    if any(statuses[kind] != "success" for kind in required):
        raise SnapshotError(COMPLETENESS_INVALID)
    if "failed" in statuses.values():
        raise SnapshotError(COMPLETENESS_INVALID)
    if any(
        observations[kind]["observation_status"] != statuses[kind] for kind in statuses
    ):
        raise SnapshotError(COMPLETENESS_INVALID)


def _validate_collection_window(envelope, now) -> tuple[datetime, datetime]:
    started = _timestamp(envelope["collection_started_at"])
    ended = _timestamp(envelope["collection_ended_at"])
    manifest = envelope["target_manifest"]
    issued, expires = (
        _timestamp(manifest["issued_at"]),
        _timestamp(manifest["expires_at"]),
    )
    maximum = getattr(
        settings,
        "TOPOLOGY_V2_MAX_COLLECTION_SPAN_SECONDS",
        DEFAULT_MAX_COLLECTION_SPAN_SECONDS,
    )
    if not issued <= started <= ended <= expires:
        raise SnapshotError(COLLECTION_WINDOW_INVALID)
    if (ended - started).total_seconds() > maximum:
        raise SnapshotError(COLLECTION_WINDOW_INVALID)
    _validate_observation_times(envelope, started, ended)
    _aware_now(now)
    return started, ended


def _validate_observation_times(envelope, started, ended) -> None:
    maximum = getattr(
        settings,
        "TOPOLOGY_V2_MAX_OBSERVATION_AGE_SECONDS",
        DEFAULT_MAX_OBSERVATION_AGE_SECONDS,
    )
    for observation in _iter_observations(envelope):
        observed = _timestamp(observation["observed_at"])
        if not started <= observed <= ended:
            raise SnapshotError(COLLECTION_WINDOW_INVALID)
        if (ended - observed).total_seconds() > maximum:
            raise SnapshotError(COLLECTION_WINDOW_INVALID)


def _iter_observations(envelope):
    for source in envelope["sources"]:
        for resource in source["resources"]:
            yield from resource["result"]["observations"]


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise SnapshotError(COLLECTION_WINDOW_INVALID) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotError(COLLECTION_WINDOW_INVALID)
    return parsed.astimezone(timezone.utc)


def _aware_now(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise SnapshotError(COLLECTION_WINDOW_INVALID)
    return timestamp.astimezone(timezone.utc)


def _prepare_redacted(envelope: dict[str, Any]) -> PreparedEnvelope:
    try:
        return prepare_stored_envelope(envelope)
    except CanonicalizationError:
        raise SnapshotError(PAYLOAD_HASH_INVALID) from None


async def ingest_direct_snapshot(
    tenant_id: str,
    topology_id: str,
    actor: str,
    raw_envelope: object,
    *,
    body_size: int,
    repository: SnapshotRepository | None = None,
    store: PayloadStore | None = None,
    now: datetime | None = None,
) -> SyncAccepted:
    validated = validate_direct_snapshot(
        topology_id, raw_envelope, body_size=body_size, now=now
    )
    repo = repository or OrmSnapshotRepository()
    timestamp = _aware_now(now)
    manifest_id = validated.envelope["target_manifest"]["manifest_id"]
    manifest = await repo.get_manifest(tenant_id, topology_id, manifest_id)
    _preflight_manifest(manifest, validated, actor, timestamp)
    profile = await repo.get_profile(
        tenant_id, topology_id, manifest.profile_revision_row_id
    )
    _validate_profile_window(profile, manifest, validated)
    payload_store = store or PayloadStore()
    prepared = await payload_store.prepare(tenant_id, raw_envelope, now=timestamp)
    if prepared.payload_hash != validated.payload_hash:
        raise SnapshotError(PAYLOAD_HASH_INVALID)
    return await commit_prepared_snapshot(
        tenant_id,
        topology_id,
        actor,
        validated,
        prepared,
        repository=repo,
        store=payload_store,
        now=now,
    )


async def commit_prepared_snapshot(
    tenant_id: str,
    topology_id: str,
    actor: str,
    validated: ValidatedSnapshot,
    prepared: PreparedPayload,
    *,
    repository: SnapshotRepository | None = None,
    store: PayloadStore | None = None,
    now: datetime | None = None,
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> SyncAccepted:
    """Atomically bind a validated prepared payload; shared with chunk commit."""
    _validate_commit_input(tenant_id, topology_id, actor, validated, prepared)
    repo, payload_store = repository or OrmSnapshotRepository(), store or PayloadStore()
    timestamp = _aware_now(now)
    async with repo.transaction() as connection:
        manifest = await repo.lock_manifest(
            tenant_id,
            topology_id,
            validated.envelope["target_manifest"]["manifest_id"],
            connection,
        )
        existing = await repo.get_snapshot(
            tenant_id, topology_id, validated.snapshot_id, connection
        )
        retry = _existing_result(existing, manifest, prepared)
        if retry is not None:
            return retry
        _require_manifest_binding(manifest, validated, actor, timestamp)
        # All payload preparation/object storage completed before this transaction.
        # Lock order is topology -> manifest -> collection -> newly created
        # analysis. Terminal collection transitions use collection -> analysis;
        # publication uses topology -> analysis and never collection.
        collection = None
        if hasattr(repo, "lock_collection_run"):
            collection = await repo.lock_collection_run(tenant_id, topology_id, actor, connection)
        await payload_store.activate(
            tenant_id, prepared, connection=connection, now=timestamp
        )
        snapshot = await repo.create_snapshot(
            _snapshot_values(
                tenant_id, actor, validated, prepared, manifest, timestamp
            ),
            connection,
        )
        run = await repo.create_analysis_run(
            _analysis_values(
                tenant_id, actor, validated, snapshot, manifest, id_factory
            ),
            connection,
        )
        await _consume_manifest(
            repo, manifest, snapshot, run, prepared, timestamp, connection
        )
        if collection is not None:
            await repo.bind_collection_analysis(collection, run, validated.snapshot_id, connection)
    return SyncAccepted(str(run.id), validated.snapshot_id)


def _validate_commit_input(tenant_id, topology_id, actor, validated, prepared) -> None:
    if not all(
        isinstance(value, str) and value for value in (tenant_id, topology_id, actor)
    ):
        raise SnapshotError(INVALID_REQUEST)
    if topology_id != validated.topology_id or tenant_id != prepared.tenant_id:
        raise SnapshotError(MANIFEST_INVALID)
    if prepared.payload_hash != validated.payload_hash:
        raise SnapshotError(PAYLOAD_HASH_INVALID)


def _existing_result(existing, manifest, prepared) -> SyncAccepted | None:
    if existing is None:
        if (
            manifest is not None
            and _enum(manifest.status) == ManifestStatus.COMMITTED.value
        ):
            raise SnapshotError(MANIFEST_CONFLICT)
        return None
    if existing.payload_hash != prepared.payload_hash:
        raise SnapshotError(SNAPSHOT_CONFLICT)
    if manifest is None or _enum(manifest.status) != ManifestStatus.COMMITTED.value:
        raise SnapshotError(SNAPSHOT_CONFLICT)
    same = (
        manifest.committed_snapshot_id == existing.snapshot_id
        and manifest.committed_payload_hash == prepared.payload_hash
        and manifest.committed_analysis_run_row_id
    )
    if not same:
        raise SnapshotError(MANIFEST_CONFLICT)
    return SyncAccepted(
        str(manifest.committed_analysis_run_row_id), existing.snapshot_id
    )


def _require_manifest_binding(manifest, validated, actor, now) -> None:
    if manifest is None or _enum(manifest.status) != ManifestStatus.ISSUED.value:
        raise SnapshotError(MANIFEST_INVALID)
    supplied = validated.envelope["target_manifest"]
    expected = _manifest_row_projection(manifest)
    if supplied != expected:
        raise SnapshotError(MANIFEST_INVALID)
    collector = validated.envelope["collector"]
    authority = (manifest.creator_subject, manifest.playbook_id, manifest.run_id)
    if authority != (actor, collector["playbook_id"], collector["run_id"]):
        raise SnapshotError(MANIFEST_INVALID)
    if now >= _timestamp(supplied["expires_at"]):
        raise SnapshotError(MANIFEST_INVALID)


def _preflight_manifest(manifest, validated, actor, now) -> None:
    if manifest is None:
        raise SnapshotError(MANIFEST_INVALID)
    status = _enum(manifest.status)
    if status == ManifestStatus.COMMITTED.value:
        return
    if status != ManifestStatus.ISSUED.value:
        raise SnapshotError(MANIFEST_INVALID)
    _require_manifest_binding(manifest, validated, actor, now)


def _validate_profile_window(profile, manifest, validated) -> None:
    if profile is None or profile.revision != manifest.profile_revision:
        raise SnapshotError(MANIFEST_INVALID)
    try:
        data = orjson.loads(profile.profile_json)
        contracts.validate_contract(data, "aioe.network-topology.profile/v1")
    except (TypeError, orjson.JSONDecodeError, contracts.ContractError):
        raise SnapshotError(MANIFEST_INVALID) from None
    span = (validated.ended_at - validated.started_at).total_seconds()
    if span > data["max_collection_span_seconds"]:
        raise SnapshotError(COLLECTION_WINDOW_INVALID)
    _validate_profile_observation_age(validated, data["max_observation_age_seconds"])


def _validate_profile_observation_age(validated, maximum) -> None:
    for observation in _iter_observations(validated.envelope):
        observed = _timestamp(observation["observed_at"])
        if (validated.ended_at - observed).total_seconds() > maximum:
            raise SnapshotError(COLLECTION_WINDOW_INVALID)


def _manifest_row_projection(row) -> dict[str, Any]:
    resources = row.expected_resources_json
    if isinstance(resources, str):
        resources = orjson.loads(resources)
    return {
        "manifest_id": row.manifest_id,
        "manifest_hash": row.manifest_hash,
        "collection_sequence": row.collection_sequence,
        "base_graph_revision": row.base_graph_revision,
        "base_publication_revision": row.base_publication_revision,
        "profile_revision": row.profile_revision,
        "issued_at": _utc_text(row.issued_at),
        "expires_at": _utc_text(row.expires_at),
        "expected_resources": resources,
    }


def _snapshot_values(tenant_id, actor, validated, prepared, manifest, now) -> dict:
    envelope = validated.stored_envelope
    target = envelope["target_manifest"]
    values = {
        "tenant_id": tenant_id,
        "topology_row_id": validated.topology_id,
        "target_manifest_row_id": manifest.id,
        "profile_revision_row_id": manifest.profile_revision_row_id,
        "payload_object_row_id": prepared.payload_object_id,
        "redacted_payload": prepared.redacted_payload,
        "snapshot_id": validated.snapshot_id,
        "schema_version": COLLECTION_SCHEMA,
        "collector_metadata": canonicalize_json(envelope["collector"]).decode(),
        "collection_started_at": validated.started_at,
        "collection_ended_at": validated.ended_at,
        "received_at": now,
        "payload_hash": prepared.payload_hash,
        "completeness_summary": canonicalize_json(envelope["completeness"]).decode(),
        "status": "RECEIVED",
        "retention_until": now + SNAPSHOT_RETENTION,
        "creator_subject": actor,
        "playbook_id": manifest.playbook_id,
        "run_id": manifest.run_id,
    }
    values.update({name: target[name] for name in _MANIFEST_SNAPSHOT_FIELDS})
    return values


_MANIFEST_SNAPSHOT_FIELDS = (
    "manifest_id",
    "manifest_hash",
    "collection_sequence",
    "base_graph_revision",
    "base_publication_revision",
    "profile_revision",
)


def _analysis_values(
    tenant_id, actor, validated, snapshot, manifest, id_factory
) -> dict:
    parser_registry = contracts.load_registry(contracts.PARSER_REGISTRY_URI)
    return {
        "tenant_id": tenant_id,
        "topology_row_id": validated.topology_id,
        "snapshot_row_id": snapshot.id,
        "target_profile_revision_row_id": manifest.profile_revision_row_id,
        "submitter_subject": actor,
        "playbook_id": manifest.playbook_id,
        "run_id": manifest.run_id,
        "analysis_kind": AnalysisKind.SNAPSHOT,
        "publication_attempt_id": str(id_factory()),
        "publish_as_current": True,
        "expected_publication_revision": manifest.base_publication_revision,
        "expected_current_graph_revision": manifest.base_graph_revision,
        "parser_bundle_version": contracts.PARSER_BUNDLE_ID,
        "input_schema_version": COLLECTION_SCHEMA,
        "facts_schema_version": analysis_runs.FACTS_V1_SCHEMA,
        "graph_schema_version": analysis_runs.GRAPH_V2_SCHEMA,
        "redaction_version": contracts.REDACTION_VERSION,
        "deterministic_parser_versions": canonicalize_json(
            parser_registry["deterministic_parsers"]
        ).decode(),
        "blocked_by": None
        if settings.TOPOLOGY_V2_ANALYSIS_ENABLED
        else "ANALYSIS_GATE",
    }


async def _consume_manifest(repo, manifest, snapshot, run, prepared, now, connection):
    manifest.status = ManifestStatus.COMMITTED
    manifest.consumed_at = now
    manifest.committed_snapshot_row_id = snapshot.id
    manifest.committed_snapshot_id = snapshot.snapshot_id
    manifest.committed_payload_hash = prepared.payload_hash
    manifest.committed_analysis_run_row_id = run.id
    await repo.save_manifest(manifest, connection)


def _utc_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _enum(value: Any) -> Any:
    return getattr(value, "value", value)


async def sync_topology_snapshot(
    request,
    *,
    repository: SnapshotRepository | None = None,
    store: PayloadStore | None = None,
    now: datetime | None = None,
):
    """Thin HTTP handler for the v2 `{id, snapshot}` config-sync branch."""
    body_size = len(getattr(request, "body", b"") or b"")
    data = None
    try:
        _validate_size(body_size)
        data = _request_payload(request)
        if not settings.TOPOLOGY_V2_INGRESS_ENABLED:
            raise SnapshotError(INGRESS_DISABLED)
        session = request.ctx.session
        result = await ingest_direct_snapshot(
            str(session["tenant_id"]),
            data["id"],
            str(session["sub"]),
            data["snapshot"],
            body_size=body_size,
            repository=repository,
            store=store,
            now=now,
        )
    except (KeyError, TypeError, SnapshotError) as error:
        snapshot_error = (
            error
            if isinstance(error, SnapshotError)
            else SnapshotError(INVALID_REQUEST)
        )
        return _error_response(snapshot_error)
    except Exception:
        return _error_response(SnapshotError(SYNC_UNAVAILABLE))
    finally:
        sanitize_config_sync_audit(request, data)
    response = {"code": 0, "data": result.as_dict()}
    contracts.validate_contract(response, RESPONSE_SCHEMA)
    return sanic_json(response, status=202, dumps=orjson.dumps)


def _request_payload(request) -> dict[str, Any]:
    data = request.json
    try:
        contracts.validate_contract(data, REQUEST_SCHEMA)
    except contracts.ContractError:
        raise SnapshotError(SCHEMA_INVALID) from None
    return dict(data)


def _error_response(error: SnapshotError):
    return sanic_json(
        {"code": 4000, "message": error.public_message},
        status=error.status,
        dumps=orjson.dumps,
    )


_UNSET = object()


def sanitize_config_sync_audit(request, data=_UNSET) -> None:
    """Replace request audit material with identifiers only, never submitted config."""
    if data is _UNSET:
        data = getattr(request, "json", None)
    summary = _audit_summary(data)
    encoded = orjson.dumps(summary)
    if hasattr(request, "_body"):
        request._body = encoded
    else:
        try:
            request.body = encoded
        except (AttributeError, TypeError):
            pass


def _audit_summary(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"config_sync": "invalid"}
    snapshot = data.get("snapshot")
    if isinstance(snapshot, dict):
        manifest = snapshot.get("target_manifest")
        manifest_id = (
            manifest.get("manifest_id") if isinstance(manifest, dict) else None
        )
        return {
            "id": data.get("id"),
            "snapshot_id": snapshot.get("snapshot_id"),
            "manifest_id": manifest_id,
            "schema_version": snapshot.get("schema_version"),
        }
    return {"id": data.get("id"), "legacy": True, "deprecated": True}
