"""Transactional compatibility rules for legacy and v2 topology documents."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tortoise.transactions import in_transaction

from models import (
    NetworkTopology,
    NetworkTopologyVersion,
    TopologyProfileRevision,
    TopologyUpgradeIntent,
)
import settings
from services.network_topology import contracts
from services.network_topology.canonicalization import canonicalize_json, sha256_uri

LEGACY_SCHEMA_VERSION = "legacy/v1"
V2_SCHEMA_VERSION = "graph/v2"
MANUAL_GENERATION_SOURCE = "manual"
PLAYBOOK_GENERATION_SOURCE = "playbook"
DEFAULT_ROOT_SCOPE_ID = "scope:organization:root"
CAPABILITY_REGISTRY_URI = "aioe.network-topology.registry/capability-profiles/v1"

TOPOLOGY_CREATE_INVALID = "TOPOLOGY_CREATE_INVALID"
TOPOLOGY_CREATE_FAILED = "TOPOLOGY_CREATE_FAILED"
TOPOLOGY_COMPATIBILITY_SEED_FAILED = "TOPOLOGY_COMPATIBILITY_SEED_FAILED"
TOPOLOGY_NAME_CONFLICT = "TOPOLOGY_NAME_CONFLICT"
TOPOLOGY_LEGACY_WRITE_REJECTED = "TOPOLOGY_LEGACY_WRITE_REJECTED"
TOPOLOGY_LEGACY_VERSION_INVALID = "TOPOLOGY_LEGACY_VERSION_INVALID"
TOPOLOGY_PUBLISH_STATE_INVALID = "TOPOLOGY_PUBLISH_STATE_INVALID"
TOPOLOGY_PUBLICATION_CAS_MISMATCH = "TOPOLOGY_PUBLICATION_CAS_MISMATCH"
TOPOLOGY_PROFILE_CAS_MISMATCH = "TOPOLOGY_PROFILE_CAS_MISMATCH"
TOPOLOGY_SCHEMA_TRANSITION_INVALID = "TOPOLOGY_SCHEMA_TRANSITION_INVALID"
TOPOLOGY_UPGRADE_INTENT_INVALID = "TOPOLOGY_UPGRADE_INTENT_INVALID"
TOPOLOGY_PUBLISH_DISABLED = "TOPOLOGY_PUBLISH_DISABLED"
TOPOLOGY_PUBLISH_FAILED = "TOPOLOGY_PUBLISH_FAILED"
LEGACY_CONVERSION_REQUIRED = "LEGACY_CONVERSION_REQUIRED"

DEFAULT_TOPOLOGY_GRAPH = {
    "nodes": [
        {
            "id": "node-1",
            "type": "process",
            "position": {"x": 0.0, "y": 0.0},
            "data": {
                "title": "新节点",
                "deviceType": "switch",
                "icon": "switch",
                "ip": "",
                "model": "",
                "version": "",
                "status": "",
                "statusColor": "#42B983",
                "isGroup": False,
                "customFields": {},
            },
        }
    ],
    "edges": [],
}
DEFAULT_TOPOLOGY_LAYOUT = {"node-1": {"x": 0.0, "y": 0.0}}
BLANK_GRAPH_HASH = sha256_uri(canonicalize_json(DEFAULT_TOPOLOGY_GRAPH))
BLANK_LAYOUT_HASH = sha256_uri(canonicalize_json(DEFAULT_TOPOLOGY_LAYOUT))

_LEGACY_WRITE_REVISION_FIELDS = (
    "resource_revision",
    "publication_revision",
    "legacy_write_generation",
)
_SCHEMA_SOURCE_FIELDS = ("document_schema_version", "generation_source")


class CompatibilityError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CompatibilityCreateResult:
    topology: Any
    version: Any
    profile_revision: Any
    is_blank: bool


async def create_compatible_topology(
    *,
    tenant_id: str,
    name: str,
    created_by: str,
    topology_json: dict | None = None,
    layout_json: dict | None = None,
) -> CompatibilityCreateResult:
    """Create the legacy resource and its v2 compatibility seeds atomically."""
    try:
        graph, layout, is_blank = _creation_document(topology_json, layout_json)
        _validate_create_identity(tenant_id, name, created_by)
    except CompatibilityError:
        raise
    except Exception:
        raise CompatibilityError(TOPOLOGY_CREATE_INVALID) from None
    try:
        profile, profile_json, profile_hash = _default_profile()
    except Exception:
        raise CompatibilityError(TOPOLOGY_CREATE_FAILED) from None
    try:
        async with in_transaction() as connection:
            await _require_unique_name(connection, tenant_id, name)
            topology = await _create_topology(
                connection, tenant_id, name, graph, layout, profile, is_blank
            )
            version = await _create_version(
                connection, topology, tenant_id, graph, layout
            )
            await _bind_initial_version(connection, topology, version)
            profile_row = await _create_profile(
                connection,
                topology,
                tenant_id,
                created_by,
                profile_json,
                profile_hash,
            )
    except CompatibilityError:
        raise
    except Exception:
        raise CompatibilityError(TOPOLOGY_CREATE_FAILED) from None
    return CompatibilityCreateResult(topology, version, profile_row, is_blank)


async def ensure_compatibility_seeds(
    *, connection: Any, topology: Any, created_by: str
) -> Any:
    """Seed legacy compatibility rows while the caller holds the topology lock."""
    if topology is None or not _bounded_text(created_by, 255):
        raise CompatibilityError(TOPOLOGY_COMPATIBILITY_SEED_FAILED)
    try:
        profile_row = await _existing_profile(connection, topology)
        if profile_row is None:
            profile_row = await _create_default_profile(
                connection, topology, created_by
            )
        fields = _apply_missing_seed_fields(topology, profile_row)
        if fields:
            await topology.save(
                using_db=connection,
                update_fields=[*fields, "utime"],
            )
        return profile_row
    except CompatibilityError:
        raise
    except Exception:
        raise CompatibilityError(TOPOLOGY_COMPATIBILITY_SEED_FAILED) from None


def require_legacy_write_allowed(topology: Any) -> None:
    """Reject legacy graph mutations once a v2 or unknown document is present."""
    if not _is_legacy_pair(topology):
        raise CompatibilityError(TOPOLOGY_LEGACY_WRITE_REJECTED)


def apply_legacy_write_revision(topology: Any) -> tuple[str, ...]:
    """Advance every CAS invalidated by a legacy graph/resource mutation."""
    require_legacy_write_allowed(topology)
    fields = list(_LEGACY_WRITE_REVISION_FIELDS)
    topology.resource_revision = _revision_value(topology, "resource_revision", 1) + 1
    topology.publication_revision = (
        _revision_value(topology, "publication_revision", 0) + 1
    )
    topology.legacy_write_generation = (
        _revision_value(topology, "legacy_write_generation", 0) + 1
    )
    if getattr(topology, "document_schema_version", None) is None:
        topology.document_schema_version = LEGACY_SCHEMA_VERSION
        topology.generation_source = MANUAL_GENERATION_SOURCE
        fields.extend(_SCHEMA_SOURCE_FIELDS)
    return tuple(fields)


def bind_current_legacy_version(topology: Any, version: Any) -> tuple[str, ...]:
    """Bind a just-created legacy version without advancing any revision."""
    require_legacy_write_allowed(topology)
    version_schema = _read(version, "document_schema_version")
    version_number = _read(version, "version")
    version_topology_id = _read(version, "topology_id")
    valid = (
        version_schema in {None, LEGACY_SCHEMA_VERSION}
        and type(version_number) is int
        and version_number == _read(topology, "revision")
        and version_topology_id == _read(topology, "id")
        and _read(version, "tenant_id") == _read(topology, "tenant_id")
        and _bounded_text(_read(version, "id"), 36)
    )
    if not valid:
        raise CompatibilityError(TOPOLOGY_LEGACY_VERSION_INVALID)
    topology.__dict__.pop("current_graph_version_row", None)
    topology.current_graph_version_row_id = _read(version, "id")
    topology.current_graph_revision = version_number
    return "current_graph_version_row_id", "current_graph_revision"


def is_blank_legacy_topology(topology: Any, version: Any | None = None) -> bool:
    """Match the exact create-time graph, layout, revisions, and version row."""
    if version is None or not _blank_topology_metadata(topology):
        return False
    if not _blank_version_metadata(topology, version):
        return False
    return _blank_payload(topology) and _blank_payload(version)


def compatibility_capabilities(topology: Any) -> dict[str, bool]:
    """Return read/write routing hints without treating unknown schemas as legacy."""
    schema = _read(topology, "document_schema_version")
    source = _read(topology, "generation_source")
    return {
        "legacy_write": _is_legacy_pair(topology),
        "v2_read": (schema, source) == (V2_SCHEMA_VERSION, PLAYBOOK_GENERATION_SOURCE),
    }


async def transition_first_v2_publish(
    *,
    connection: Any,
    topology: Any,
    legacy_version: Any,
    expected_revision: int,
    expected_publication_revision: int,
    expected_profile_revision: int,
    analysis_state: str,
    published_by: str,
    published_at: datetime,
    upgrade_intent: Any | None = None,
) -> tuple[str, ...]:
    """Internal hook requiring a caller-held transaction and locked topology rows."""
    _validate_publish_request(analysis_state, published_by, published_at)
    _validate_publish_cas(
        topology,
        legacy_version,
        expected_revision,
        expected_publication_revision,
        expected_profile_revision,
    )
    blank = is_blank_legacy_topology(topology, legacy_version)
    if not blank and upgrade_intent is None:
        raise CompatibilityError(LEGACY_CONVERSION_REQUIRED)
    if upgrade_intent is not None:
        _validate_upgrade_intent(upgrade_intent, topology, legacy_version, published_at)
    await _persist_transition(
        connection, topology, upgrade_intent, published_by, published_at
    )
    return _SCHEMA_SOURCE_FIELDS


async def publish_first_v2_transaction(
    *,
    tenant_id: str,
    topology_id: str,
    legacy_version_id: str,
    expected_revision: int,
    expected_publication_revision: int,
    expected_profile_revision: int,
    analysis_state: str,
    published_by: str,
    published_at: datetime,
    upgrade_intent_id: str | None = None,
) -> tuple[str, ...]:
    """Lock legacy publication rows and switch schema/source in one transaction."""
    _require_publish_enabled()
    async with in_transaction() as connection:
        _require_publish_enabled()
        topology = await _lock_publish_topology(connection, tenant_id, topology_id)
        version = await _lock_publish_version(
            connection, tenant_id, topology_id, legacy_version_id
        )
        intent = await _lock_upgrade_intent(
            connection, tenant_id, topology_id, upgrade_intent_id
        )
        return await transition_first_v2_publish(
            connection=connection,
            topology=topology,
            legacy_version=version,
            expected_revision=expected_revision,
            expected_publication_revision=expected_publication_revision,
            expected_profile_revision=expected_profile_revision,
            analysis_state=analysis_state,
            published_by=published_by,
            published_at=published_at,
            upgrade_intent=intent,
        )


def _require_publish_enabled() -> None:
    if not settings.TOPOLOGY_V2_PUBLISH_ENABLED:
        raise CompatibilityError(TOPOLOGY_PUBLISH_DISABLED)


async def _lock_publish_topology(connection: Any, tenant_id: str, topology_id: str):
    topology = await (
        NetworkTopology.filter(
            id=topology_id, tenant_id=tenant_id, deleted=False
        )
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if topology is None:
        raise CompatibilityError(TOPOLOGY_PUBLICATION_CAS_MISMATCH)
    return topology


async def _lock_publish_version(
    connection: Any, tenant_id: str, topology_id: str, version_id: str
):
    version = await (
        NetworkTopologyVersion.filter(
            id=version_id,
            topology_id=topology_id,
            tenant_id=tenant_id,
            deleted=False,
        )
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if version is None:
        raise CompatibilityError(TOPOLOGY_PUBLICATION_CAS_MISMATCH)
    return version


async def _lock_upgrade_intent(
    connection: Any,
    tenant_id: str,
    topology_id: str,
    intent_id: str | None,
):
    if intent_id is None:
        return None
    intent = await (
        TopologyUpgradeIntent.filter(
            id=intent_id,
            topology_row_id=topology_id,
            tenant_id=tenant_id,
            deleted=False,
        )
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if intent is None:
        raise CompatibilityError(TOPOLOGY_UPGRADE_INTENT_INVALID)
    return intent


def _creation_document(
    topology_json: dict | None, layout_json: dict | None
) -> tuple[dict, dict, bool]:
    if topology_json is None and layout_json is None:
        graph = deepcopy(DEFAULT_TOPOLOGY_GRAPH)
        layout = deepcopy(DEFAULT_TOPOLOGY_LAYOUT)
        is_blank = True
    elif type(topology_json) is dict and type(layout_json) is dict:
        graph = deepcopy(topology_json)
        layout = deepcopy(layout_json)
        is_blank = False
    else:
        raise CompatibilityError(TOPOLOGY_CREATE_INVALID)
    _payload_hashes(graph, layout)
    return graph, layout, is_blank


def _validate_create_identity(tenant_id: str, name: str, created_by: str) -> None:
    if not _bounded_text(tenant_id, 255) or not _bounded_text(created_by, 255):
        raise CompatibilityError(TOPOLOGY_CREATE_INVALID)
    if not _bounded_text(name, 200):
        raise CompatibilityError(TOPOLOGY_CREATE_INVALID)


def _default_profile() -> tuple[dict, str, str]:
    profile = contracts.load_registry(CAPABILITY_REGISTRY_URI)["default_profile"]
    contracts.validate_contract(profile, "aioe.network-topology.profile/v1")
    canonical = canonicalize_json(profile)
    if profile.get("root_scope_seed") != DEFAULT_ROOT_SCOPE_ID:
        raise ValueError("Default profile root scope does not match")
    return profile, canonical.decode(), sha256_uri(canonical)


async def _require_unique_name(connection: Any, tenant_id: str, name: str) -> None:
    query = NetworkTopology.filter(name=name, tenant_id=tenant_id, deleted=False)
    if await query.using_db(connection).exists():
        raise CompatibilityError(TOPOLOGY_NAME_CONFLICT)


async def _create_topology(
    connection: Any,
    tenant_id: str,
    name: str,
    graph: dict,
    layout: dict,
    profile: dict,
    is_blank: bool,
) -> Any:
    return await NetworkTopology.create(
        using_db=connection,
        tenant_id=tenant_id,
        name=name,
        source_config="",
        topology_json=graph,
        layout_json=layout,
        source_hash=None,
        revision=1,
        last_sync_at=None,
        document_schema_version=LEGACY_SCHEMA_VERSION,
        generation_source=MANUAL_GENERATION_SOURCE,
        root_scope_id=profile["root_scope_seed"],
        topology_profile_revision=1,
        resource_revision=1 if is_blank else 2,
        publication_revision=0 if is_blank else 1,
        legacy_write_generation=0 if is_blank else 1,
    )


async def _create_version(
    connection: Any, topology: Any, tenant_id: str, graph: dict, layout: dict
) -> Any:
    return await NetworkTopologyVersion.create(
        using_db=connection,
        topology=topology,
        tenant_id=tenant_id,
        version=1,
        name=topology.name,
        topology_json=deepcopy(graph),
        layout_json=deepcopy(layout),
        source_hash=None,
        change_type="create",
        change_summary="创建拓扑",
        document_schema_version=LEGACY_SCHEMA_VERSION,
    )


async def _bind_initial_version(connection: Any, topology: Any, version: Any) -> None:
    fields = bind_current_legacy_version(topology, version)
    await topology.save(
        using_db=connection,
        update_fields=[*fields, "utime"],
    )


async def _create_profile(
    connection: Any,
    topology: Any,
    tenant_id: str,
    created_by: str,
    profile_json: str,
    profile_hash: str,
) -> Any:
    return await TopologyProfileRevision.create(
        using_db=connection,
        topology_row=topology,
        tenant_id=tenant_id,
        revision=1,
        resource_revision=1,
        profile_json=profile_json,
        profile_hash=profile_hash,
        created_by=created_by,
    )


async def _existing_profile(connection: Any, topology: Any) -> Any | None:
    rows = await (
        TopologyProfileRevision.filter(
            topology_row_id=_read(topology, "id"),
            tenant_id=_read(topology, "tenant_id"),
            revision=1,
            deleted=False,
        )
        .using_db(connection)
        .select_for_update()
        .limit(2)
    )
    if type(rows) is not list or len(rows) > 1:
        raise CompatibilityError(TOPOLOGY_COMPATIBILITY_SEED_FAILED)
    return rows[0] if rows else None


async def _create_default_profile(
    connection: Any, topology: Any, created_by: str
) -> Any:
    _, profile_json, profile_hash = _default_profile()
    return await _create_profile(
        connection,
        topology,
        _read(topology, "tenant_id"),
        created_by,
        profile_json,
        profile_hash,
    )


def _apply_missing_seed_fields(topology: Any, profile_row: Any) -> tuple[str, ...]:
    fields = []
    if _read(topology, "root_scope_id") is None:
        topology.root_scope_id = _profile_root_scope(profile_row)
        fields.append("root_scope_id")
    if _read(topology, "topology_profile_revision") is None:
        topology.topology_profile_revision = _read(profile_row, "revision")
        fields.append("topology_profile_revision")
    return tuple(fields)


def _profile_root_scope(profile_row: Any) -> str:
    try:
        profile = json.loads(_read(profile_row, "profile_json"))
        contracts.validate_contract(profile, "aioe.network-topology.profile/v1")
        canonical = canonicalize_json(profile)
    except Exception:
        raise CompatibilityError(TOPOLOGY_COMPATIBILITY_SEED_FAILED) from None
    valid = (
        sha256_uri(canonical) == _read(profile_row, "profile_hash")
        and profile.get("root_scope_seed") == DEFAULT_ROOT_SCOPE_ID
    )
    if not valid:
        raise CompatibilityError(TOPOLOGY_COMPATIBILITY_SEED_FAILED)
    return profile["root_scope_seed"]


def _blank_topology_metadata(topology: Any) -> bool:
    return (
        getattr(topology, "document_schema_version", None) == LEGACY_SCHEMA_VERSION
        and getattr(topology, "generation_source", None) == MANUAL_GENERATION_SOURCE
        and getattr(topology, "root_scope_id", None) == DEFAULT_ROOT_SCOPE_ID
        and getattr(topology, "revision", None) == 1
        and getattr(topology, "topology_profile_revision", None) == 1
        and getattr(topology, "resource_revision", None) == 1
        and getattr(topology, "publication_revision", None) == 0
        and getattr(topology, "current_graph_revision", None) == 1
    )


def _blank_version_metadata(topology: Any, version: Any) -> bool:
    topology_id = getattr(topology, "id", None)
    return (
        _bounded_text(getattr(version, "id", None), 36)
        and getattr(version, "version", None) == 1
        and getattr(topology, "current_graph_version_row_id", None)
        == getattr(version, "id", None)
        and getattr(version, "topology_id", None) == topology_id
        and getattr(version, "tenant_id", None) == getattr(topology, "tenant_id", None)
        and getattr(version, "document_schema_version", None) == LEGACY_SCHEMA_VERSION
        and getattr(version, "change_type", None) == "create"
    )


def _blank_payload(row: Any) -> bool:
    try:
        hashes = _payload_hashes(row.topology_json, row.layout_json)
    except (AttributeError, TypeError, ValueError):
        return False
    return hashes == (BLANK_GRAPH_HASH, BLANK_LAYOUT_HASH)


def _payload_hashes(graph: dict, layout: dict) -> tuple[str, str]:
    return (
        sha256_uri(canonicalize_json(graph)),
        sha256_uri(canonicalize_json(layout)),
    )


def _revision_value(row: Any, field: str, default: int) -> int:
    value = getattr(row, field, None)
    if value is None:
        return default
    if type(value) is not int or value < 0:
        raise CompatibilityError(TOPOLOGY_LEGACY_WRITE_REJECTED)
    return value


def _validate_publish_request(state: str, actor: str, at: datetime) -> None:
    valid_time = isinstance(at, datetime) and at.tzinfo is not None
    if state != "SUCCEEDED" or not _bounded_text(actor, 255) or not valid_time:
        raise CompatibilityError(TOPOLOGY_PUBLISH_STATE_INVALID)


def _validate_publish_cas(
    topology: Any,
    version: Any,
    expected_revision: int,
    expected_publication: int,
    expected_profile: int,
) -> None:
    if getattr(topology, "document_schema_version", None) != LEGACY_SCHEMA_VERSION:
        raise CompatibilityError(TOPOLOGY_SCHEMA_TRANSITION_INVALID)
    if getattr(topology, "generation_source", None) != MANUAL_GENERATION_SOURCE:
        raise CompatibilityError(TOPOLOGY_SCHEMA_TRANSITION_INVALID)
    if getattr(topology, "revision", None) != expected_revision:
        raise CompatibilityError(TOPOLOGY_PUBLICATION_CAS_MISMATCH)
    if getattr(topology, "publication_revision", None) != expected_publication:
        raise CompatibilityError(TOPOLOGY_PUBLICATION_CAS_MISMATCH)
    if getattr(topology, "topology_profile_revision", None) != expected_profile:
        raise CompatibilityError(TOPOLOGY_PROFILE_CAS_MISMATCH)
    if getattr(version, "version", None) != expected_revision:
        raise CompatibilityError(TOPOLOGY_PUBLICATION_CAS_MISMATCH)
    if getattr(version, "document_schema_version", None) != LEGACY_SCHEMA_VERSION:
        raise CompatibilityError(TOPOLOGY_SCHEMA_TRANSITION_INVALID)
    if not _current_version_matches(topology, version):
        raise CompatibilityError(TOPOLOGY_PUBLICATION_CAS_MISMATCH)


def _validate_upgrade_intent(
    intent: Any, topology: Any, legacy_version: Any, published_at: datetime
) -> None:
    expires_at = getattr(intent, "expires_at", None)
    valid_expiry = isinstance(expires_at, datetime) and expires_at.tzinfo is not None
    expected = (
        getattr(intent, "tenant_id", None) == getattr(topology, "tenant_id", None)
        and getattr(intent, "topology_row_id", None) == getattr(topology, "id", None)
        and getattr(intent, "legacy_graph_version_row_id", None)
        == getattr(legacy_version, "id", None)
        and getattr(legacy_version, "id", None) is not None
        and getattr(intent, "expected_legacy_revision", None)
        == getattr(topology, "revision", None)
        and getattr(intent, "status", None) == "ACTIVE"
        and getattr(intent, "deleted", None) is False
        and getattr(intent, "consumed_at", None) is None
        and getattr(intent, "cancelled_at", None) is None
        and valid_expiry
    )
    try:
        unexpired = bool(expected and expires_at > published_at)
    except TypeError:
        unexpired = False
    if not unexpired:
        raise CompatibilityError(TOPOLOGY_UPGRADE_INTENT_INVALID)


async def _persist_transition(
    connection: Any,
    topology: Any,
    intent: Any | None,
    published_by: str,
    published_at: datetime,
) -> None:
    old_topology = (topology.document_schema_version, topology.generation_source)
    old_intent = _intent_state(intent)
    try:
        topology.document_schema_version = V2_SCHEMA_VERSION
        topology.generation_source = PLAYBOOK_GENERATION_SOURCE
        await topology.save(
            using_db=connection,
            update_fields=[*_SCHEMA_SOURCE_FIELDS, "utime"],
        )
        if intent is not None:
            await _consume_intent(connection, intent, published_by, published_at)
    except Exception:
        topology.document_schema_version, topology.generation_source = old_topology
        _restore_intent(intent, old_intent)
        raise CompatibilityError(TOPOLOGY_PUBLISH_FAILED) from None


async def _consume_intent(
    connection: Any, intent: Any, published_by: str, published_at: datetime
) -> None:
    intent.status = "CONSUMED"
    intent.consumed_at = published_at
    intent.consumed_by = published_by
    await intent.save(
        using_db=connection,
        update_fields=["status", "consumed_at", "consumed_by", "utime"],
    )


def _intent_state(intent: Any | None) -> tuple[Any, Any, Any] | None:
    if intent is None:
        return None
    return intent.status, intent.consumed_at, intent.consumed_by


def _restore_intent(intent: Any | None, state: tuple[Any, Any, Any] | None) -> None:
    if intent is not None and state is not None:
        intent.status, intent.consumed_at, intent.consumed_by = state


def _bounded_text(value: Any, maximum: int) -> bool:
    return (
        isinstance(value, str) and value == value.strip() and 0 < len(value) <= maximum
    )


def _is_legacy_pair(row: Any) -> bool:
    pair = (_read(row, "document_schema_version"), _read(row, "generation_source"))
    return pair in {
        (None, None),
        (LEGACY_SCHEMA_VERSION, MANUAL_GENERATION_SOURCE),
    }


def _current_version_matches(topology: Any, version: Any) -> bool:
    version_id = _read(version, "id")
    return (
        _bounded_text(version_id, 36)
        and _read(topology, "current_graph_version_row_id") == version_id
        and _read(topology, "current_graph_revision") == _read(version, "version")
        and _read(version, "topology_id") == _read(topology, "id")
        and _read(version, "tenant_id") == _read(topology, "tenant_id")
    )


def _read(row: Any, field: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(field, default)
    return getattr(row, field, default)
