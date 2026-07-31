"""Atomic graph/v2 publication with READY/LIVE payload activation."""

from __future__ import annotations

import logging
import json
from collections.abc import Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from services.network_topology.canonicalization import canonicalize_json
from services.network_topology.versions import (
    PublishingError,
    build_graph_diff,
    compact_change_summary,
    facts_hash,
    graph_hash,
    scope_items,
    validate_publication,
)

SUPERSEDED = "SUPERSEDED"
SUCCEEDED = "SUCCEEDED"
LEGACY_SCHEMA_VERSION = "legacy/v1"
V2_SCHEMA_VERSION = "graph/v2"
PLAYBOOK_GENERATION_SOURCE = "playbook"
DEFAULT_INLINE_THRESHOLD = 256 * 1024
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PublicationRequest:
    tenant_id: str
    topology_id: str
    analysis_run_id: str
    snapshot_id: str
    profile_revision_id: str
    lease_owner: str
    fencing_token: int
    collection_sequence: int
    expected_graph_revision: int
    expected_publication_revision: int
    facts: Mapping[str, Any]
    graph: Mapping[str, Any]
    profile: Mapping[str, Any]
    actor: str
    profile_revision: int = 1
    lease_generation: int | None = None
    upgrade_intent_id: str | None = None
    aliases: Mapping[str, str] = field(default_factory=dict)
    require_non_empty: bool = True


@dataclass(frozen=True, slots=True)
class PublicationResult:
    status: str
    created_version: bool
    version_id: str | None
    graph_revision: int
    publication_revision: int


@dataclass(frozen=True, slots=True)
class _PreparedDocument:
    inline_payload: str | None
    object_payload: Any | None


class FailureInjector:
    """Deterministic fault hook used by focused publication tests."""

    def __init__(self, points=()) -> None:
        self.points = set(points)

    def hit(self, point: str) -> None:
        if point in self.points:
            raise RuntimeError(point)


class PublicationRepository(Protocol):
    async def publication_base(self, request): ...
    def transaction(self): ...
    async def lock_publication(self, request, connection): ...
    async def lock_layout_set(self, topology, connection): ...
    async def lock_upgrade_intent(self, request, connection): ...
    async def max_version(self, topology, connection): ...
    async def current_graph(self, topology, connection): ...
    async def create_version(self, values, connection): ...
    async def save_publication(self, topology, run, values, connection): ...
    async def supersede(self, run, reason, connection): ...
    async def add_audit(self, values, connection): ...


class PublicationObjectStore(Protocol):
    async def prepare(self, tenant_id, kind, document): ...
    async def activate(self, prepared, connection): ...
    async def delete(self, row): ...


class ImmutablePublicationStore:
    """Canonical document adapter over the existing payload two-phase store."""

    def __init__(self, payload_store=None) -> None:
        if payload_store is None:
            from services.network_topology.payload_store import PayloadStore

            payload_store = PayloadStore()
        self.payload_store = payload_store

    async def prepare(self, tenant_id, kind, document):
        del kind
        from services.network_topology.payload_store import PreparedEnvelope

        canonical = canonicalize_json(document)
        envelope = PreparedEnvelope(
            normalized=deepcopy(document),
            canonical_bytes=canonical,
            payload_hash=graph_hash(document),
        )
        return await self.payload_store._prepare_object(
            tenant_id, envelope, datetime.now(timezone.utc)
        )

    async def activate(self, prepared, connection):
        return await self.payload_store.activate(
            prepared.tenant_id, prepared, connection=connection
        )

    async def delete(self, row):
        deleting = await self.payload_store.repository.mark_deleting_if_unreferenced(
            row.tenant_id, row.id
        )
        if deleting is None:
            return False
        await self.payload_store.backend.delete(deleting.object_key)
        await self.payload_store.repository.soft_delete(deleting)
        return True


async def publish_graph(
    request: PublicationRequest,
    repository: PublicationRepository | None = None,
    object_store: PublicationObjectStore | None = None,
    *,
    inline_threshold: int = DEFAULT_INLINE_THRESHOLD,
    clock=lambda: datetime.now(timezone.utc),
) -> PublicationResult:
    """Publish one validated graph or mark a stale publication superseded."""
    _validate_request(request, inline_threshold)
    validate_publication(
        request.facts,
        request.graph,
        request.profile,
        request.profile_revision,
        require_non_empty=request.require_non_empty,
    )
    repo = repository or OrmPublishingRepository()
    store = object_store or ImmutablePublicationStore()
    base_graph = await repo.publication_base(request)
    prepared = await _prepare_documents(
        request, store, inline_threshold, base_graph
    )
    async with repo.transaction() as connection:
        now = clock()
        locked = await repo.lock_publication(request, connection)
        topology, run, snapshot, profile = locked
        if _already_committed(request, topology):
            return _existing_result(topology)
        stale = _stale_reason(request, topology, run, snapshot, profile, now)
        if stale:
            await repo.supersede(run, stale, connection)
            return _superseded_result(topology)
        layout_set = await repo.lock_layout_set(topology, connection)
        _validate_layout_pointer(topology, layout_set)
        intent = await repo.lock_upgrade_intent(request, connection)
        current_graph = await repo.current_graph(topology, connection)
        _validate_current_pointer(topology, current_graph)
        if current_graph != base_graph:
            await repo.supersede(run, "CURRENT_POINTER_CHANGED", connection)
            return _superseded_result(topology)
        return await _commit_publication(
            request, prepared, repo, store, connection, topology, run,
            snapshot, profile, layout_set, intent, current_graph, now
        )


def _validate_request(request, inline_threshold):
    if type(request) is not PublicationRequest or inline_threshold < 0:
        raise PublishingError("PUBLICATION_REQUEST_INVALID")
    texts = (
        request.tenant_id, request.topology_id, request.analysis_run_id,
        request.snapshot_id, request.profile_revision_id, request.lease_owner,
        request.actor,
    )
    revisions = (
        request.fencing_token, request.collection_sequence,
        request.expected_graph_revision, request.expected_publication_revision,
        request.profile_revision,
    )
    if any(not isinstance(value, str) or not value for value in texts):
        raise PublishingError("PUBLICATION_REQUEST_INVALID")
    if any(type(value) is not int or value < 0 for value in revisions):
        raise PublishingError("PUBLICATION_REQUEST_INVALID")
    if not isinstance(request.aliases, Mapping):
        raise PublishingError("PUBLICATION_REQUEST_INVALID")


async def _prepare_documents(request, store, threshold, base_graph):
    diff = build_graph_diff(base_graph, request.graph)
    facts = await _prepare_document(
        request.tenant_id, "facts", request.facts, store, threshold
    )
    if graph_hash(request.graph) == _optional_graph_hash(base_graph):
        graph = _PreparedDocument(None, None)
    else:
        graph_envelope = {"presentation_graph": request.graph, "diff": diff}
        graph = await _prepare_document(
            request.tenant_id, "graph", graph_envelope, store, threshold
        )
    return facts, graph, diff


def _optional_graph_hash(graph):
    return graph_hash(graph) if isinstance(graph, Mapping) else None


async def _prepare_document(tenant_id, kind, document, store, threshold):
    canonical = canonicalize_json(document)
    if len(canonical) <= threshold:
        return _PreparedDocument(canonical.decode(), None)
    prepared = await store.prepare(tenant_id, kind, document)
    return _PreparedDocument(None, prepared)


def _already_committed(request, topology):
    return (
        getattr(topology, "current_analysis_run_row_id", None) == request.analysis_run_id
        and getattr(topology, "current_snapshot_row_id", None) == request.snapshot_id
        and int(getattr(topology, "last_published_collection_sequence", 0) or 0)
        == request.collection_sequence
    )


def _stale_reason(request, topology, run, snapshot, profile, now):
    checks = (
        (getattr(run, "id", None) == request.analysis_run_id, "RUN_POINTER_CHANGED"),
        (getattr(snapshot, "id", None) == request.snapshot_id, "SNAPSHOT_POINTER_CHANGED"),
        (getattr(profile, "id", None) == request.profile_revision_id, "PROFILE_CHANGED"),
        (getattr(topology, "id", None) == request.topology_id, "TOPOLOGY_CHANGED"),
        (getattr(topology, "tenant_id", None) == request.tenant_id, "TENANT_CHANGED"),
        (getattr(run, "state", None) == "RUNNING", "RUN_STATE_CHANGED"),
        (getattr(run, "stage", None) == "PUBLISHING", "RUN_STAGE_CHANGED"),
        (getattr(run, "lease_owner", None) == request.lease_owner, "LEASE_CHANGED"),
        (int(getattr(run, "fencing_token", -1)) == request.fencing_token, "FENCING_CHANGED"),
        (int(snapshot.collection_sequence) == request.collection_sequence, "SEQUENCE_CHANGED"),
        (_optional_equal(snapshot, "profile_revision", request.profile_revision), "PROFILE_CHANGED"),
        (_optional_equal(snapshot, "profile_revision_row_id", request.profile_revision_id), "PROFILE_CHANGED"),
        (_optional_equal(run, "snapshot_row_id", request.snapshot_id), "SNAPSHOT_POINTER_CHANGED"),
        (_optional_equal(run, "target_profile_revision_row_id", request.profile_revision_id), "PROFILE_CHANGED"),
        (_optional_equal(run, "facts_schema_version", "aioe.network-topology.facts/v1"), "FACTS_SCHEMA_CHANGED"),
        (_optional_equal(run, "graph_schema_version", "aioe.network-topology.graph/v2"), "GRAPH_SCHEMA_CHANGED"),
        (_optional_truthy(run, "publish_as_current"), "PUBLISH_TARGET_CHANGED"),
        (int(profile.revision) == request.profile_revision, "PROFILE_CHANGED"),
        (_profile_matches(profile, request.profile), "PROFILE_CHANGED"),
        (int(topology.topology_profile_revision or 0) == request.profile_revision, "PROFILE_CHANGED"),
        (int(run.expected_current_graph_revision) == request.expected_graph_revision, "GRAPH_EXPECTATION_CHANGED"),
        (int(topology.current_graph_revision or 0) == request.expected_graph_revision, "GRAPH_REVISION_CHANGED"),
        (int(run.expected_publication_revision) == request.expected_publication_revision, "PUBLICATION_EXPECTATION_CHANGED"),
        (int(topology.publication_revision or 0) == request.expected_publication_revision, "PUBLICATION_REVISION_CHANGED"),
        (request.collection_sequence > int(topology.last_published_collection_sequence or 0), "NEWER_SEQUENCE_PUBLISHED"),
    )
    for valid, reason in checks:
        if not valid:
            return reason
    if request.lease_generation is not None and int(run.lease_generation) != request.lease_generation:
        return "LEASE_GENERATION_CHANGED"
    expires_at = getattr(run, "lease_expires_at", None)
    if expires_at is not None and expires_at <= now:
        return "LEASE_EXPIRED"
    if getattr(run, "cancellation_requested", False):
        return "CANCELLATION_REQUESTED"
    return None


def _optional_equal(row, field, expected):
    value = getattr(row, field, None)
    return value is None or str(value) == str(expected)


def _optional_truthy(row, field):
    return not hasattr(row, field) or getattr(row, field) is True


def _profile_matches(row, expected):
    raw = getattr(row, "profile_json", None)
    if raw is None:
        return True
    try:
        document = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return False
    if document != expected:
        return False
    expected_hash = getattr(row, "profile_hash", None)
    return expected_hash is None or graph_hash(document) == expected_hash


def _validate_current_pointer(topology, current_graph):
    current_hash = getattr(topology, "graph_hash", None)
    current_pointer = getattr(topology, "current_graph_version_row_id", None)
    if current_hash is not None and current_pointer is None:
        raise PublishingError("CURRENT_POINTER_INVALID")
    if current_hash is not None and graph_hash(current_graph) != current_hash:
        raise PublishingError("CURRENT_POINTER_INVALID")


def _validate_layout_pointer(topology, layout_set):
    row_id = getattr(topology, "current_layout_set_row_id", None)
    if (row_id is None) != (layout_set is None):
        raise PublishingError("LAYOUT_POINTER_INVALID")
    if layout_set is None:
        return
    valid = (
        str(layout_set.id) == str(row_id)
        and int(layout_set.layout_set_revision)
        == int(topology.current_layout_set_revision or 0)
    )
    if not valid:
        raise PublishingError("LAYOUT_POINTER_INVALID")


async def _commit_publication(
    request, prepared, repo, store, connection, topology, run, snapshot,
    profile, layout_set, intent, current_graph, now,
):
    diff = prepared[2]
    new_hash = graph_hash(request.graph)
    same_hash = new_hash == getattr(topology, "graph_hash", None)
    graph_revision = int(topology.current_graph_revision or 0) + (not same_hash)
    publication_revision = int(topology.publication_revision or 0) + 1
    published_layout = layout_set
    if not same_hash:
        published_layout = await _rebase_layout(
            repo, topology, layout_set, request, graph_revision, connection
        )
    await _transition_schema(
        repo, request, topology, run, intent, connection, now
    )
    activated = await _activate_documents(prepared, store, connection)
    await _bind_facts_payload(repo, run, prepared[0], activated[0], connection)
    version = None
    if not same_hash:
        version = await _create_graph_version(
            repo, request, prepared, topology, snapshot, profile,
            published_layout, diff, new_hash, publication_revision, connection
        )
    values = _topology_values(
        request, version, snapshot, run, published_layout, new_hash,
        graph_revision, publication_revision, now
    )
    await repo.save_publication(topology, run, values, connection)
    await repo.add_audit(_audit_values(request, version, new_hash, now), connection)
    return PublicationResult(
        SUCCEEDED, version is not None, _row_id(version),
        graph_revision, publication_revision
    )


async def _rebase_layout(repo, topology, layout_set, request, revision, connection):
    method = getattr(repo, "rebase_layout_set", None)
    if method is None:
        return layout_set
    return await method(
        topology, layout_set, scope_items(request.graph), request.aliases,
        revision, request.actor, connection
    )


async def _transition_schema(repo, request, topology, run, intent, connection, now):
    if getattr(topology, "document_schema_version", None) != LEGACY_SCHEMA_VERSION:
        return
    method = getattr(repo, "transition_schema", None)
    if method is None:
        raise PublishingError("UPGRADE_INTENT_INVALID")
    try:
        await method(request, topology, run, intent, connection, now)
    except Exception as exc:
        if isinstance(exc, PublishingError):
            raise
        raise PublishingError("UPGRADE_INTENT_INVALID") from None


async def _activate_documents(prepared, store, connection):
    activated = []
    for document in prepared[:2]:
        if document.object_payload is not None:
            row = await store.activate(document.object_payload, connection)
            activated.append(row)
        else:
            activated.append(None)
    return activated


async def _bind_facts_payload(repo, run, prepared, row, connection):
    method = getattr(repo, "bind_facts_payload", None)
    if method is not None:
        await method(run, prepared, row, connection)


async def _create_graph_version(
    repo, request, prepared, topology, snapshot, profile, layout_set,
    diff, new_hash, publication_revision, connection,
):
    number = await repo.max_version(topology, connection) + 1
    root_layout = await _root_layout(repo, topology, layout_set, request.graph, connection)
    graph_document = _graph_payload(request.graph, diff, prepared[1])
    values = {
        "topology_id": topology.id, "tenant_id": request.tenant_id,
        "version": number, "name": topology.name,
        "topology_json": deepcopy(request.graph), "layout_json": root_layout,
        "source_hash": None, "change_type": "publish",
        "change_summary": compact_change_summary(diff),
        "document_schema_version": V2_SCHEMA_VERSION,
        "presentation_graph_payload": graph_document,
        "presentation_graph_payload_object_row_id": _object_id(prepared[1]),
        "snapshot_row_id": snapshot.id, "analysis_run_row_id": request.analysis_run_id,
        "profile_revision_row_id": profile.id, "facts_hash": facts_hash(request.facts),
        "graph_hash": new_hash, "published_collection_sequence": request.collection_sequence,
        "publication_revision": publication_revision,
        "published_layout_set_row_id": _row_id(layout_set),
    }
    return await repo.create_version(values, connection)


def _graph_payload(graph, diff, prepared):
    if prepared.object_payload is not None:
        return None
    return canonicalize_json({"presentation_graph": graph, "diff": diff}).decode()


async def _root_layout(repo, topology, layout_set, graph, connection):
    method = getattr(repo, "root_layout", None)
    if method is not None:
        return await method(topology, layout_set, graph, connection)
    layout = getattr(layout_set, "root_layout", {}) if layout_set else {}
    valid = scope_items(graph).get(graph["root_scope_id"], set())
    return {key: deepcopy(value) for key, value in layout.items() if key in valid}


def _topology_values(
    request, version, snapshot, run, layout_set, new_hash,
    graph_revision, publication_revision, now,
):
    values = {
        "topology_json": deepcopy(request.graph),
        "document_schema_version": V2_SCHEMA_VERSION,
        "generation_source": PLAYBOOK_GENERATION_SOURCE,
        "current_snapshot_row_id": snapshot.id,
        "current_analysis_run_row_id": run.id,
        "current_layout_set_row_id": _row_id(layout_set),
        "current_layout_set_revision": getattr(layout_set, "layout_set_revision", None),
        "current_graph_revision": graph_revision,
        "publication_revision": publication_revision,
        "fact_hash": facts_hash(request.facts), "graph_hash": new_hash,
        "last_published_collection_sequence": request.collection_sequence,
        "last_sync_at": now,
    }
    if version is not None:
        values["current_graph_version_row_id"] = version.id
        values["revision"] = version.version
        values["layout_json"] = version.layout_json
    return values


def _audit_values(request, version, new_hash, now):
    return {
        "event": "network_topology.graph_published",
        "tenant_id": request.tenant_id, "topology_id": request.topology_id,
        "analysis_run_id": request.analysis_run_id,
        "snapshot_id": request.snapshot_id, "version_id": _row_id(version),
        "collection_sequence": request.collection_sequence,
        "graph_hash": new_hash, "published_at": now,
    }


def _existing_result(topology):
    return PublicationResult(
        SUCCEEDED, False, getattr(topology, "current_graph_version_row_id", None),
        int(topology.current_graph_revision or 0), int(topology.publication_revision or 0)
    )


def _superseded_result(topology):
    return PublicationResult(
        SUPERSEDED, False, None, int(topology.current_graph_revision or 0),
        int(topology.publication_revision or 0)
    )


def _row_id(row):
    return getattr(row, "id", None) if row is not None else None


def _object_id(prepared):
    value = prepared.object_payload
    if value is None:
        return None
    return getattr(value, "payload_object_id", getattr(value, "id", None))


async def sweep_publication_objects(rows, store) -> int:
    """Delete only unreferenced non-LIVE publication objects."""
    deleted = 0
    for row in rows:
        if getattr(row, "deleted", False) or getattr(row, "referenced", False):
            continue
        if str(getattr(row, "status", "")) not in {"READY", "STAGING", "DELETING"}:
            continue
        result = await store.delete(row)
        deleted += result is not False
    return deleted


class OrmPublishingRepository:
    @asynccontextmanager
    async def transaction(self):
        from tortoise.transactions import in_transaction

        async with in_transaction() as connection:
            yield connection

    async def lock_publication(self, request, connection):
        topology = await _lock_topology(request, connection)
        run = await _lock_run(request, connection)
        snapshot = await _get_snapshot(request, connection)
        profile = await _get_profile(request, connection)
        if any(item is None for item in (topology, run, snapshot, profile)):
            raise PublishingError("PUBLICATION_BINDING_MISSING")
        return topology, run, snapshot, profile

    async def lock_layout_set(self, topology, connection):
        from models import TopologyLayoutSet

        row_id = topology.current_layout_set_row_id
        if row_id is None:
            return None
        return await TopologyLayoutSet.filter(
            id=row_id, tenant_id=topology.tenant_id,
            topology_row_id=topology.id, deleted=False,
        ).using_db(connection).select_for_update().first()

    async def lock_upgrade_intent(self, request, connection):
        from models import TopologyUpgradeIntent

        if request.upgrade_intent_id is None:
            return None
        return await TopologyUpgradeIntent.filter(
            id=request.upgrade_intent_id, tenant_id=request.tenant_id,
            topology_row_id=request.topology_id, deleted=False,
        ).using_db(connection).select_for_update().first()

    async def max_version(self, topology, connection):
        from models import NetworkTopologyVersion

        row = await NetworkTopologyVersion.filter(
            topology_id=topology.id, tenant_id=topology.tenant_id, deleted=False
        ).using_db(connection).order_by("-version").first()
        return int(row.version) if row is not None else 0

    async def current_graph(self, topology, connection):
        del connection
        return deepcopy(topology.topology_json) if topology.graph_hash else None

    async def create_version(self, values, connection):
        from models import NetworkTopologyVersion

        return await NetworkTopologyVersion.create(using_db=connection, **values)

    async def save_publication(self, topology, run, values, connection):
        from models import NetworkTopology

        for key, value in values.items():
            setattr(topology, key, value)
        updated = await NetworkTopology.filter(
            id=topology.id, tenant_id=topology.tenant_id, deleted=False
        ).using_db(connection).update(**values, utime=values["last_sync_at"])
        if updated != 1:
            raise PublishingError("PUBLICATION_CAS_FAILED")
        run.state = SUCCEEDED
        run.graph_hash = values["graph_hash"]
        run.facts_hash = values["fact_hash"]
        run.finished_at = values["last_sync_at"]
        await run.save(
            using_db=connection,
            update_fields=["state", "graph_hash", "facts_hash", "finished_at", "utime"],
        )

    async def supersede(self, run, reason, connection):
        run.state = SUPERSEDED
        run.error_code = reason
        run.retryable = False
        run.finished_at = datetime.now(timezone.utc)
        await run.save(
            using_db=connection,
            update_fields=["state", "error_code", "retryable", "finished_at", "utime"],
        )

    async def add_audit(self, values, connection):
        del connection
        _LOGGER.info("topology publication committed", extra={"publication": values})

    async def transition_schema(self, request, topology, run, intent, connection, now):
        from models import NetworkTopologyVersion
        from services.network_topology.compatibility_manager import (
            transition_first_v2_publish,
        )

        version = await NetworkTopologyVersion.filter(
            id=topology.current_graph_version_row_id, tenant_id=request.tenant_id,
            topology_id=topology.id, deleted=False,
        ).using_db(connection).select_for_update().first()
        if version is None:
            raise PublishingError("CURRENT_POINTER_INVALID")
        await transition_first_v2_publish(
            connection=connection, topology=topology, legacy_version=version,
            expected_revision=request.expected_graph_revision,
            expected_publication_revision=request.expected_publication_revision,
            expected_profile_revision=request.profile_revision,
            analysis_state=SUCCEEDED, published_by=request.actor,
            published_at=now, upgrade_intent=intent,
        )

    async def rebase_layout_set(
        self, topology, layout_set, items, aliases, revision, actor, connection
    ):
        from models import TopologyLayoutSet
        from services.network_topology.layouts import (
            decode_scope_version_map,
            encode_json,
        )

        current = {} if layout_set is None else decode_scope_version_map(
            layout_set.scope_version_map_json
        )
        mapped = await _rebase_scope_versions(
            topology, current, items, aliases, revision, actor, connection
        )
        set_revision = int(topology.current_layout_set_revision or 0) + 1
        return await TopologyLayoutSet.create(
            using_db=connection, tenant_id=topology.tenant_id,
            topology_row_id=topology.id, layout_set_revision=set_revision,
            scope_version_map_json=encode_json(mapped), created_by=actor,
        )

    async def root_layout(self, topology, layout_set, graph, connection):
        from services.network_topology.layouts import decode_scope_version_map

        if layout_set is None:
            return {}
        mapping = decode_scope_version_map(layout_set.scope_version_map_json)
        entry = mapping.get(graph["root_scope_id"])
        document = await _layout_document(topology, entry, connection)
        valid = scope_items(graph)[graph["root_scope_id"]]
        return _legacy_root_layout(document, valid)

    async def publication_base(self, request):
        from models import NetworkTopology

        topology = await NetworkTopology.filter(
            id=request.topology_id, tenant_id=request.tenant_id, deleted=False
        ).first()
        if topology is None:
            raise PublishingError("PUBLICATION_BINDING_MISSING")
        return deepcopy(topology.topology_json) if topology.graph_hash else None

    async def bind_facts_payload(self, run, prepared, row, connection):
        run.fact_graph_payload = prepared.inline_payload
        run.fact_graph_payload_object_row = row
        await run.save(
            using_db=connection,
            update_fields=[
                "fact_graph_payload", "fact_graph_payload_object_row", "utime"
            ],
        )


async def _lock_topology(request, connection):
    from models import NetworkTopology

    return await NetworkTopology.filter(
        id=request.topology_id, tenant_id=request.tenant_id, deleted=False
    ).using_db(connection).select_for_update().first()


async def _lock_run(request, connection):
    from models import TopologyAnalysisRun

    return await TopologyAnalysisRun.filter(
        id=request.analysis_run_id, tenant_id=request.tenant_id,
        topology_row_id=request.topology_id, deleted=False,
    ).using_db(connection).select_for_update().first()


async def _get_snapshot(request, connection):
    from models import TopologyCollectionSnapshot

    return await TopologyCollectionSnapshot.filter(
        id=request.snapshot_id, tenant_id=request.tenant_id,
        topology_row_id=request.topology_id, deleted=False,
    ).using_db(connection).first()


async def _get_profile(request, connection):
    from models import TopologyProfileRevision

    return await TopologyProfileRevision.filter(
        id=request.profile_revision_id, tenant_id=request.tenant_id,
        topology_row_id=request.topology_id, deleted=False,
    ).using_db(connection).first()


async def _rebase_scope_versions(
    topology, current, items, aliases, revision, actor, connection
):
    from models import TopologyScopeLayoutVersion
    from services.network_topology.layouts import encode_json

    result = {}
    for scope_id in sorted(set(current) | set(items)):
        old = await _layout_document(topology, current.get(scope_id), connection)
        document = _rebase_document(old, items.get(scope_id, set()), aliases)
        scope_revision = int(current.get(scope_id, {}).get("scope_layout_revision", 0)) + 1
        row = await TopologyScopeLayoutVersion.create(
            using_db=connection, tenant_id=topology.tenant_id,
            topology_row_id=topology.id, scope_id=scope_id,
            scope_layout_revision=scope_revision, graph_revision=revision,
            layout_json=encode_json(document), viewport_json=encode_json(document["viewport"]),
            created_by=actor,
        )
        result[scope_id] = {
            "layout_version_row_id": str(row.id),
            "scope_layout_revision": scope_revision, "graph_revision": revision,
        }
    return result


async def _layout_document(topology, entry, connection):
    from models import TopologyScopeLayoutVersion
    from services.network_topology.layouts import decode_layout_document

    if not entry:
        return _empty_layout()
    row = await TopologyScopeLayoutVersion.filter(
        id=entry.get("layout_version_row_id"), tenant_id=topology.tenant_id,
        topology_row_id=topology.id, deleted=False,
    ).using_db(connection).first()
    if row is None:
        raise PublishingError("LAYOUT_POINTER_INVALID")
    return decode_layout_document(row.layout_json)


def _rebase_document(old, valid_ids, aliases):
    valid = set(valid_ids)
    items, orphans = {}, dict(old.get("orphan_items", {}))
    for stable_id, value in old.get("items", {}).items():
        target = aliases.get(stable_id, stable_id)
        if target in valid and target not in items:
            items[target] = deepcopy(value)
        elif stable_id not in valid:
            orphans[stable_id] = deepcopy(value)
    for stable_id in valid:
        orphans.pop(stable_id, None)
    return {
        "items": items, "orphan_items": orphans,
        "pending_item_ids": sorted(valid - set(items)),
        "viewport": deepcopy(old.get("viewport", {"x": 0, "y": 0, "zoom": 1})),
        "algorithm": old.get("algorithm", "dagre"),
    }


def _empty_layout():
    return {
        "items": {}, "orphan_items": {}, "pending_item_ids": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1}, "algorithm": "dagre",
    }


def _legacy_root_layout(document, valid):
    output = {}
    for stable_id, value in document.get("items", {}).items():
        if stable_id in valid:
            output[stable_id] = {key: value[key] for key in ("x", "y") if key in value}
    return output
