"""Immutable scope layouts and presentation-only override reconciliation."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Set
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import orjson
from tortoise.transactions import in_transaction

from models._network_topology_v2_presentation import (
    PresentationOverride,
    TopologyLayoutSet,
    TopologyScopeLayoutVersion,
)
from models.base import parse_encrypted_json
from models.network_topology import NetworkTopology


LAYOUT_INVALID_REQUEST = "LAYOUT_INVALID_REQUEST"
LAYOUT_NOT_FOUND = "LAYOUT_NOT_FOUND"
LAYOUT_GRAPH_CONFLICT = "LAYOUT_GRAPH_CONFLICT"
LAYOUT_SCOPE_CONFLICT = "LAYOUT_SCOPE_CONFLICT"
LAYOUT_SET_CONFLICT = "LAYOUT_SET_CONFLICT"
OVERRIDE_KIND_FORBIDDEN = "OVERRIDE_KIND_FORBIDDEN"
OVERRIDE_VALUE_INVALID = "OVERRIDE_VALUE_INVALID"
OVERRIDE_SCOPE_FORBIDDEN = "OVERRIDE_SCOPE_FORBIDDEN"

ALLOWED_OVERRIDE_KINDS = frozenset(
    {"display_name", "hidden", "highlighted", "note", "color", "scope_mapping"}
)
_PUBLIC_ERRORS = {
    LAYOUT_INVALID_REQUEST: (400, "布局请求不合法"),
    LAYOUT_NOT_FOUND: (404, "网络拓扑不存在"),
    LAYOUT_GRAPH_CONFLICT: (409, "拓扑图版本已更新"),
    LAYOUT_SCOPE_CONFLICT: (409, "当前范围布局已更新"),
    LAYOUT_SET_CONFLICT: (409, "布局集合已更新"),
    OVERRIDE_KIND_FORBIDDEN: (422, "展示覆盖字段不受支持"),
    OVERRIDE_VALUE_INVALID: (422, "展示覆盖值不合法"),
    OVERRIDE_SCOPE_FORBIDDEN: (403, "展示覆盖范围未授权"),
}
_LOGGER = logging.getLogger(__name__)


class LayoutError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        self.status, self.public_message = _PUBLIC_ERRORS[code]
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LayoutAuthority:
    tenant_id: str
    topology_id: str
    actor: str


@dataclass(frozen=True, slots=True)
class LayoutSaveResult:
    scope_layout_revision: int
    layout_set_revision: int
    graph_revision: int


@dataclass(frozen=True, slots=True)
class OverrideReconcileResult:
    migrated: int
    orphaned: int
    active: int


class LayoutRepository(Protocol):
    def transaction(self): ...
    async def get_topology(self, tenant_id, topology_id, *, connection, lock): ...
    async def get_layout_set(
        self, tenant_id, topology_id, row_id, *, connection, lock
    ): ...
    async def get_layout_version(
        self, tenant_id, topology_id, row_id, *, connection
    ): ...
    async def create_layout_version(self, values, *, connection): ...
    async def create_layout_set(self, values, *, connection): ...
    async def cas_layout_pointer(self, topology, **kwargs): ...
    async def list_active_overrides(self, authority, *, connection, lock): ...
    async def save_override(self, row, fields, *, connection): ...
    async def create_override(self, values, *, connection): ...


class OrmLayoutRepository:
    @asynccontextmanager
    async def transaction(self):
        async with in_transaction() as connection:
            yield connection

    async def get_topology(self, tenant_id, topology_id, *, connection, lock):
        query = NetworkTopology.filter(
            id=topology_id, tenant_id=tenant_id, deleted=False
        ).using_db(connection)
        if lock:
            query = query.select_for_update()
        return await query.first()

    async def get_layout_set(
        self, tenant_id, topology_id, row_id, *, connection, lock
    ):
        if row_id is None:
            return None
        query = TopologyLayoutSet.filter(
            id=row_id,
            tenant_id=tenant_id,
            topology_row_id=topology_id,
            deleted=False,
        ).using_db(connection)
        if lock:
            query = query.select_for_update()
        return await query.first()

    async def get_layout_version(
        self, tenant_id, topology_id, row_id, *, connection
    ):
        return await TopologyScopeLayoutVersion.filter(
            id=row_id,
            tenant_id=tenant_id,
            topology_row_id=topology_id,
            deleted=False,
        ).using_db(connection).first()

    async def create_layout_version(self, values, *, connection):
        return await TopologyScopeLayoutVersion.create(using_db=connection, **values)

    async def create_layout_set(self, values, *, connection):
        return await TopologyLayoutSet.create(using_db=connection, **values)

    async def cas_layout_pointer(
        self,
        topology,
        *,
        expected_row_id,
        expected_set_revision,
        expected_graph_revision,
        new_set,
        next_graph_revision,
        connection,
    ):
        filters = _cas_filters(
            topology, expected_row_id, expected_set_revision, expected_graph_revision
        )
        values = {
            "current_layout_set_row_id": new_set.id,
            "current_layout_set_revision": new_set.layout_set_revision,
        }
        if next_graph_revision is not None:
            values["current_graph_revision"] = next_graph_revision
        updated = await NetworkTopology.filter(**filters).using_db(connection).update(
            **values
        )
        return updated == 1

    async def list_active_overrides(self, authority, *, connection, lock):
        query = PresentationOverride.filter(
            tenant_id=authority.tenant_id,
            topology_row_id=authority.topology_id,
            status="ACTIVE",
            deleted=False,
        ).using_db(connection)
        if lock:
            query = query.select_for_update()
        return await query.all()

    async def save_override(self, row, fields, *, connection):
        await row.save(using_db=connection, update_fields=[*fields, "utime"])

    async def create_override(self, values, *, connection):
        return await PresentationOverride.create(using_db=connection, **values)


def _cas_filters(topology, row_id, set_revision, graph_revision):
    return {
        "id": topology.id,
        "tenant_id": topology.tenant_id,
        "deleted": False,
        "current_layout_set_row_id": row_id,
        "current_layout_set_revision": set_revision,
        "current_graph_revision": graph_revision,
    }


def encode_json(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def decode_scope_version_map(value: object) -> dict[str, dict[str, Any]]:
    document = parse_encrypted_json(value, default={})
    if not isinstance(document, dict):
        raise LayoutError(LAYOUT_SET_CONFLICT)
    if not all(isinstance(key, str) and isinstance(item, dict) for key, item in document.items()):
        raise LayoutError(LAYOUT_SET_CONFLICT)
    return {key: dict(item) for key, item in document.items()}


def decode_layout_document(value: object) -> dict[str, Any]:
    document = parse_encrypted_json(value, default=None)
    if not isinstance(document, dict):
        raise LayoutError(LAYOUT_SET_CONFLICT)
    return document


async def save_scope_layout(
    authority: LayoutAuthority,
    payload: object,
    *,
    repository: LayoutRepository | None = None,
) -> LayoutSaveResult:
    _validate_authority(authority)
    request = _validate_layout_request(payload)
    repo = repository or OrmLayoutRepository()
    async with repo.transaction() as connection:
        state = await _locked_state(repo, authority, connection)
        _require_graph(state.topology, request["graph_revision"])
        current_revision = _scope_revision(state.scope_map, request["scope_id"])
        if current_revision != request["expected_scope_layout_revision"]:
            raise LayoutError(LAYOUT_SCOPE_CONFLICT)
        old_document = await _scope_document(
            repo, authority, state.scope_map.get(request["scope_id"]), connection
        )
        document = _merge_saved_layout(old_document, request)
        return await _commit_scope_layout(
            repo, authority, state, request, document, connection
        )


@dataclass(frozen=True, slots=True)
class _LockedState:
    topology: Any
    layout_set: Any
    scope_map: dict[str, dict[str, Any]]
    set_revision: int
    set_row_id: str | None


async def _locked_state(repo, authority, connection) -> _LockedState:
    topology = await repo.get_topology(
        authority.tenant_id, authority.topology_id, connection=connection, lock=True
    )
    if topology is None:
        raise LayoutError(LAYOUT_NOT_FOUND)
    row_id = getattr(topology, "current_layout_set_row_id", None)
    layout_set = await repo.get_layout_set(
        authority.tenant_id,
        authority.topology_id,
        row_id,
        connection=connection,
        lock=True,
    )
    revision = int(getattr(topology, "current_layout_set_revision", 0) or 0)
    if (row_id is None) != (layout_set is None):
        raise LayoutError(LAYOUT_SET_CONFLICT)
    if layout_set is not None and int(layout_set.layout_set_revision) != revision:
        raise LayoutError(LAYOUT_SET_CONFLICT)
    scope_map = {} if layout_set is None else decode_scope_version_map(
        layout_set.scope_version_map_json
    )
    return _LockedState(topology, layout_set, scope_map, revision, row_id)


async def _commit_scope_layout(repo, authority, state, request, document, connection):
    scope_revision = request["expected_scope_layout_revision"] + 1
    version = await repo.create_layout_version(
        _layout_version_values(authority, request, document, scope_revision),
        connection=connection,
    )
    scope_map = dict(state.scope_map)
    scope_map[request["scope_id"]] = _scope_entry(version, scope_revision, request["graph_revision"])
    new_set = await repo.create_layout_set(
        _layout_set_values(authority, state.set_revision + 1, scope_map),
        connection=connection,
    )
    await _cas_or_conflict(
        repo, state, new_set, request["graph_revision"], None, connection
    )
    return LayoutSaveResult(scope_revision, new_set.layout_set_revision, request["graph_revision"])


def _layout_version_values(authority, request, document, scope_revision):
    return {
        "tenant_id": authority.tenant_id,
        "topology_row_id": authority.topology_id,
        "scope_id": request["scope_id"],
        "scope_layout_revision": scope_revision,
        "graph_revision": request["graph_revision"],
        "layout_json": encode_json(document),
        "viewport_json": encode_json(request["viewport"]),
        "created_by": authority.actor,
    }


def _layout_set_values(authority, revision, scope_map):
    return {
        "tenant_id": authority.tenant_id,
        "topology_row_id": authority.topology_id,
        "layout_set_revision": revision,
        "scope_version_map_json": encode_json(scope_map),
        "created_by": authority.actor,
    }


def _scope_entry(version, scope_revision, graph_revision):
    return {
        "layout_version_row_id": str(version.id),
        "scope_layout_revision": scope_revision,
        "graph_revision": graph_revision,
    }


async def _cas_or_conflict(repo, state, new_set, graph_revision, next_graph, connection):
    updated = await repo.cas_layout_pointer(
        state.topology,
        expected_row_id=state.set_row_id,
        expected_set_revision=state.set_revision,
        expected_graph_revision=graph_revision,
        new_set=new_set,
        next_graph_revision=next_graph,
        connection=connection,
    )
    if not updated:
        raise LayoutError(LAYOUT_SET_CONFLICT)


async def rebase_layouts_for_graph(
    authority: LayoutAuthority,
    *,
    expected_graph_revision: int,
    graph_revision: int,
    scope_items: Mapping[str, Set[str]],
    repository: LayoutRepository | None = None,
) -> LayoutSaveResult:
    _validate_rebase(authority, expected_graph_revision, graph_revision, scope_items)
    repo = repository or OrmLayoutRepository()
    async with repo.transaction() as connection:
        state = await _locked_state(repo, authority, connection)
        _require_graph(state.topology, expected_graph_revision)
        scope_map = await _rebase_scope_map(
            repo, authority, state.scope_map, scope_items, graph_revision, connection
        )
        new_set = await repo.create_layout_set(
            _layout_set_values(authority, state.set_revision + 1, scope_map),
            connection=connection,
        )
        await _cas_or_conflict(
            repo, state, new_set, expected_graph_revision, graph_revision, connection
        )
        return LayoutSaveResult(0, new_set.layout_set_revision, graph_revision)


async def _rebase_scope_map(repo, authority, current, scope_items, graph_revision, connection):
    result = {}
    for scope_id in sorted(set(current) | set(scope_items)):
        entry = current.get(scope_id)
        old_document = await _scope_document(repo, authority, entry, connection)
        document = _rebase_document(old_document, scope_items.get(scope_id, set()))
        revision = _scope_revision(current, scope_id) + 1
        request = {
            "scope_id": scope_id,
            "graph_revision": graph_revision,
            "viewport": document["viewport"],
        }
        version = await repo.create_layout_version(
            _layout_version_values(authority, request, document, revision),
            connection=connection,
        )
        result[scope_id] = _scope_entry(version, revision, graph_revision)
    return result


async def _scope_document(repo, authority, entry, connection):
    if entry is None:
        return _empty_layout_document()
    row_id = entry.get("layout_version_row_id")
    row = await repo.get_layout_version(
        authority.tenant_id, authority.topology_id, row_id, connection=connection
    )
    if row is None:
        raise LayoutError(LAYOUT_SET_CONFLICT)
    return decode_layout_document(row.layout_json)


def _empty_layout_document():
    return {
        "items": {},
        "orphan_items": {},
        "pending_item_ids": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "algorithm": "dagre",
    }


def _merge_saved_layout(old, request):
    items = {key: dict(value) for key, value in request["items"].items()}
    orphans = dict(old.get("orphan_items", {}))
    for stable_id in items:
        orphans.pop(stable_id, None)
    pending = sorted(set(old.get("pending_item_ids", [])) - set(items))
    return {
        "items": items,
        "orphan_items": orphans,
        "pending_item_ids": pending,
        "viewport": dict(request["viewport"]),
        "algorithm": request["algorithm"],
    }


def _rebase_document(old, valid_ids):
    valid = set(valid_ids)
    old_items = old.get("items", {})
    items = {key: dict(value) for key, value in old_items.items() if key in valid}
    orphans = dict(old.get("orphan_items", {}))
    orphans.update({key: dict(value) for key, value in old_items.items() if key not in valid})
    for stable_id in valid:
        orphans.pop(stable_id, None)
    return {
        "items": items,
        "orphan_items": orphans,
        "pending_item_ids": sorted(valid - set(items)),
        "viewport": dict(old.get("viewport", {"x": 0, "y": 0, "zoom": 1})),
        "algorithm": old.get("algorithm", "dagre"),
    }


def _scope_revision(scope_map, scope_id):
    entry = scope_map.get(scope_id)
    if entry is None:
        return 0
    revision = entry.get("scope_layout_revision")
    if type(revision) is not int or revision < 1:
        raise LayoutError(LAYOUT_SET_CONFLICT)
    return revision


def _require_graph(topology, graph_revision):
    if int(getattr(topology, "current_graph_revision", 0) or 0) != graph_revision:
        raise LayoutError(LAYOUT_GRAPH_CONFLICT)


def _validate_authority(authority):
    if type(authority) is not LayoutAuthority:
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    if not all(_is_text(value) for value in (authority.tenant_id, authority.topology_id, authority.actor)):
        raise LayoutError(LAYOUT_INVALID_REQUEST)


def _validate_layout_request(payload):
    if not isinstance(payload, Mapping):
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    required = {"scope_id", "expected_scope_layout_revision", "graph_revision", "items", "viewport", "algorithm"}
    if set(payload) != required:
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    request = dict(payload)
    if not _is_text(request["scope_id"]):
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    _validate_revisions(request)
    _validate_items(request["items"])
    _validate_viewport(request["viewport"])
    if request["algorithm"] not in {"dagre", "manual"}:
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    return request


def _validate_revisions(request):
    expected = request["expected_scope_layout_revision"]
    graph = request["graph_revision"]
    if type(expected) is not int or expected < 0:
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    if type(graph) is not int or graph < 1:
        raise LayoutError(LAYOUT_INVALID_REQUEST)


def _validate_items(items):
    if not isinstance(items, Mapping) or len(items) > 5000:
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    for stable_id, item in items.items():
        if not _is_text(stable_id) or not isinstance(item, Mapping):
            raise LayoutError(LAYOUT_INVALID_REQUEST)
        if set(item) != {"x", "y", "pinned"}:
            raise LayoutError(LAYOUT_INVALID_REQUEST)
        if not _number(item["x"]) or not _number(item["y"]):
            raise LayoutError(LAYOUT_INVALID_REQUEST)
        if type(item["pinned"]) is not bool:
            raise LayoutError(LAYOUT_INVALID_REQUEST)


def _validate_viewport(viewport):
    if not isinstance(viewport, Mapping) or set(viewport) != {"x", "y", "zoom"}:
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    if not _number(viewport["x"]) or not _number(viewport["y"]):
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    if not _number(viewport["zoom"]) or viewport["zoom"] <= 0:
        raise LayoutError(LAYOUT_INVALID_REQUEST)


def _validate_rebase(authority, expected, graph, scope_items):
    _validate_authority(authority)
    if type(expected) is not int or expected < 0:
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    if type(graph) is not int or graph <= expected:
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    if not isinstance(scope_items, Mapping):
        raise LayoutError(LAYOUT_INVALID_REQUEST)
    for scope_id, stable_ids in scope_items.items():
        if not _is_text(scope_id) or not isinstance(stable_ids, Set):
            raise LayoutError(LAYOUT_INVALID_REQUEST)
        if not all(_is_text(stable_id) for stable_id in stable_ids):
            raise LayoutError(LAYOUT_INVALID_REQUEST)


def validate_override(kind: str, value: object, authorized_scope_ids: Set[str]):
    if kind not in ALLOWED_OVERRIDE_KINDS:
        raise LayoutError(OVERRIDE_KIND_FORBIDDEN)
    if kind in {"display_name", "note"}:
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise LayoutError(OVERRIDE_VALUE_INVALID)
    elif kind in {"hidden", "highlighted"}:
        if type(value) is not bool:
            raise LayoutError(OVERRIDE_VALUE_INVALID)
    elif kind == "color":
        if not _valid_color(value):
            raise LayoutError(OVERRIDE_VALUE_INVALID)
    else:
        _validate_scope_mapping(value, authorized_scope_ids)
    return value


def _valid_color(value):
    if not isinstance(value, str) or len(value) not in {4, 7} or value[0] != "#":
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value[1:])


def _validate_scope_mapping(value, authorized_scope_ids):
    if not isinstance(value, Mapping) or set(value) != {"scope_ids"}:
        raise LayoutError(OVERRIDE_VALUE_INVALID)
    scope_ids = value["scope_ids"]
    if not isinstance(scope_ids, list) or not all(_is_text(item) for item in scope_ids):
        raise LayoutError(OVERRIDE_VALUE_INVALID)
    if not set(scope_ids).issubset(set(authorized_scope_ids)):
        raise LayoutError(OVERRIDE_SCOPE_FORBIDDEN)


async def reconcile_presentation_overrides(
    authority: LayoutAuthority,
    *,
    valid_stable_ids: Set[str],
    aliases: Mapping[str, str],
    authorized_scope_ids: Set[str],
    repository: LayoutRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    warn: Callable[[str], None] = _LOGGER.warning,
) -> OverrideReconcileResult:
    _validate_authority(authority)
    repo = repository or OrmLayoutRepository()
    migrated = orphaned = active = 0
    async with repo.transaction() as connection:
        rows = await repo.list_active_overrides(
            authority, connection=connection, lock=True
        )
        for row in rows:
            target = _resolve_alias(row.target_stable_id, aliases)
            if target in valid_stable_ids and target == row.target_stable_id:
                active += 1
            elif target in valid_stable_ids:
                await _migrate_override(repo, row, target, connection)
                migrated += 1
            else:
                await _orphan_override(repo, row, clock(), warn, connection)
                orphaned += 1
    return OverrideReconcileResult(migrated, orphaned, active)


async def _migrate_override(repo, row, target, connection):
    row.status = "MIGRATED"
    row.migrated_to_target_stable_id = target
    await repo.save_override(
        row, ("status", "migrated_to_target_stable_id"), connection=connection
    )
    values = {
        "tenant_id": row.tenant_id,
        "topology_row_id": row.topology_row_id,
        "profile_revision_row_id": row.profile_revision_row_id,
        "target_stable_id": target,
        "override_kind": row.override_kind,
        "override_value": row.override_value,
        "status": "ACTIVE",
        "created_by": row.created_by,
    }
    await repo.create_override(values, connection=connection)


async def _orphan_override(repo, row, timestamp, warn, connection):
    row.status = "ORPHANED"
    row.orphaned_at = timestamp
    await repo.save_override(
        row, ("status", "orphaned_at"), connection=connection
    )
    warn(f"Orphaned presentation override {row.id}")


def _resolve_alias(target, aliases):
    seen = set()
    while target in aliases and target not in seen:
        seen.add(target)
        target = aliases[target]
    return target


def _number(value):
    return type(value) in {int, float} and value == value and abs(value) != float("inf")


def _is_text(value):
    return isinstance(value, str) and value == value.strip() and bool(value)


__all__ = [
    "ALLOWED_OVERRIDE_KINDS",
    "LAYOUT_GRAPH_CONFLICT",
    "LAYOUT_SCOPE_CONFLICT",
    "LAYOUT_SET_CONFLICT",
    "OVERRIDE_KIND_FORBIDDEN",
    "OVERRIDE_SCOPE_FORBIDDEN",
    "LayoutAuthority",
    "LayoutError",
    "LayoutSaveResult",
    "OrmLayoutRepository",
    "OverrideReconcileResult",
    "decode_layout_document",
    "decode_scope_version_map",
    "encode_json",
    "rebase_layouts_for_graph",
    "reconcile_presentation_overrides",
    "save_scope_layout",
    "validate_override",
]
