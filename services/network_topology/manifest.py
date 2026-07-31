"""Transactional issuance of immutable topology collection manifests."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import Any

import orjson
from tortoise.transactions import in_transaction

import settings
from models import (
    AssetInstance,
    NetworkTopology,
    NetworkTopologyVersion,
    TopologyCollectionManifest,
    TopologyProfileRevision,
    TopologyUpgradeIntent,
)
from services.network_topology import compatibility_manager as compatibility
from services.network_topology import contracts
from services.network_topology.canonicalization import (
    CanonicalizationError,
    canonicalize_and_hash_manifest,
    canonicalize_json,
    sha256_uri,
)

MANIFEST_REQUEST_SCHEMA = "aioe.network-topology.api/manifest-request/v1"
MANIFEST_RESPONSE_SCHEMA = "aioe.network-topology.api/manifest-response/v1"
CAPABILITY_REGISTRY_URI = "aioe.network-topology.registry/capability-profiles/v1"
MANIFEST_TTL_SECONDS = 300

MANIFEST_INVALID_REQUEST = "MANIFEST_INVALID_REQUEST"
MANIFEST_AUTHORITY_INVALID = "MANIFEST_AUTHORITY_INVALID"
MANIFEST_INGRESS_DISABLED = "MANIFEST_INGRESS_DISABLED"
MANIFEST_TOPOLOGY_NOT_FOUND = "MANIFEST_TOPOLOGY_NOT_FOUND"
MANIFEST_PROFILE_STALE = "MANIFEST_PROFILE_STALE"
MANIFEST_RESOURCE_NOT_FOUND = "MANIFEST_RESOURCE_NOT_FOUND"
MANIFEST_CAPABILITY_UNSUPPORTED = "MANIFEST_CAPABILITY_UNSUPPORTED"
MANIFEST_CAPABILITY_AMBIGUOUS = "MANIFEST_CAPABILITY_AMBIGUOUS"
MANIFEST_UPGRADE_INTENT_REQUIRED = "MANIFEST_UPGRADE_INTENT_REQUIRED"
MANIFEST_UPGRADE_INTENT_INVALID = "MANIFEST_UPGRADE_INTENT_INVALID"
MANIFEST_ISSUE_FAILED = "MANIFEST_ISSUE_FAILED"

_PUBLIC_ERRORS = {
    MANIFEST_INVALID_REQUEST: (400, "Manifest 请求不合法"),
    MANIFEST_AUTHORITY_INVALID: (403, "Manifest 签发权限无效"),
    MANIFEST_INGRESS_DISABLED: (503, "Manifest 签发入口未启用"),
    MANIFEST_TOPOLOGY_NOT_FOUND: (404, "网络拓扑不存在"),
    MANIFEST_PROFILE_STALE: (409, "拓扑 Profile 已更新"),
    MANIFEST_RESOURCE_NOT_FOUND: (404, "拓扑采集资源不存在"),
    MANIFEST_CAPABILITY_UNSUPPORTED: (422, "资源采集能力不受支持"),
    MANIFEST_CAPABILITY_AMBIGUOUS: (409, "资源采集能力存在歧义"),
    MANIFEST_UPGRADE_INTENT_REQUIRED: (409, "非空旧拓扑需要升级意图"),
    MANIFEST_UPGRADE_INTENT_INVALID: (409, "拓扑升级意图无效"),
    MANIFEST_ISSUE_FAILED: (503, "Manifest 暂时无法签发"),
}


class ManifestError(RuntimeError):
    """Stable public failure without source payload or persistence details."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.status, self.public_message = _PUBLIC_ERRORS[code]
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class ManifestAuthority:
    tenant_id: str
    topology_id: str
    subject: str
    playbook_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class ManifestIssueResult:
    target_manifest: dict[str, Any]
    capability_registry_version: str


def authority_from_user_session(
    session: object, payload: object
) -> ManifestAuthority:
    """Freeze manifest scope from the authenticated user and request contract."""
    try:
        provenance = payload["selection_provenance"]
        values = (
            session["tenant_id"],
            payload["id"],
            str(session["sub"]),
            provenance["playbook_id"],
            provenance["run_id"],
        )
    except (KeyError, TypeError):
        raise ManifestError(MANIFEST_AUTHORITY_INVALID) from None
    if not all(_is_text(value) for value in values):
        raise ManifestError(MANIFEST_AUTHORITY_INVALID) from None
    return ManifestAuthority(*values)


async def issue_manifest(
    authority: ManifestAuthority,
    payload: object,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(
        microsecond=0
    ),
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> ManifestIssueResult:
    """Validate and persist one server-derived collection contract."""
    request = _validate_request(authority, payload)
    manifest_id = _read_manifest_id(id_factory)
    try:
        async with in_transaction() as connection:
            result = await _issue_locked(
                connection, authority, request, clock, manifest_id
            )
    except ManifestError:
        raise
    except Exception:
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    return result


async def _issue_locked(connection, authority, request, clock, manifest_id):
    topology = await _locked_topology(connection, authority)
    _require_ingress()
    profile_row, profile = await _current_profile(
        connection, authority, topology, request["profile_revision"]
    )
    resources = await _locked_resources(
        connection, authority.tenant_id, request["resolved_resource_ids"], profile
    )
    registry_version = _validated_registry_version()
    expected_resources = _expected_resources(resources)
    upgrade_intent, now = await _upgrade_intent(connection, authority, topology, clock)
    sequence = await _next_sequence(connection, authority, topology)
    target = _target_manifest(
        topology,
        profile_row,
        expected_resources,
        sequence,
        manifest_id,
        now,
        registry_version,
    )
    await _persist_manifest(
        connection,
        authority,
        topology,
        profile_row,
        upgrade_intent,
        request["selection_provenance"],
        target,
        registry_version,
    )
    response = {"code": 0, "data": {"target_manifest": target}}
    contracts.validate_contract(response, MANIFEST_RESPONSE_SCHEMA)
    return ManifestIssueResult(target, registry_version)


def _validate_request(authority: object, payload: object) -> dict[str, Any]:
    if type(authority) is not ManifestAuthority:
        raise ManifestError(MANIFEST_AUTHORITY_INVALID) from None
    try:
        contracts.validate_contract(payload, MANIFEST_REQUEST_SCHEMA)
    except contracts.ContractError:
        raise ManifestError(MANIFEST_INVALID_REQUEST) from None
    request = dict(payload)
    provenance = request["selection_provenance"]
    identifiers = (
        request["id"],
        provenance["playbook_id"],
        provenance["run_id"],
        *request["resolved_resource_ids"],
    )
    if len(request["resolved_resource_ids"]) > 100 or not all(
        _is_text(value) for value in identifiers
    ):
        raise ManifestError(MANIFEST_INVALID_REQUEST) from None
    expected = (authority.topology_id, authority.playbook_id, authority.run_id)
    actual = (request["id"], provenance["playbook_id"], provenance["run_id"])
    if actual != expected:
        raise ManifestError(MANIFEST_AUTHORITY_INVALID) from None
    return request


def _require_ingress() -> None:
    if not settings.TOPOLOGY_V2_INGRESS_ENABLED:
        raise ManifestError(MANIFEST_INGRESS_DISABLED) from None


async def _locked_topology(connection, authority):
    topology = await (
        NetworkTopology.filter(
            id=authority.topology_id,
            tenant_id=authority.tenant_id,
            deleted=False,
        )
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if topology is None:
        raise ManifestError(MANIFEST_TOPOLOGY_NOT_FOUND) from None
    return topology


async def _current_profile(connection, authority, topology, revision):
    if getattr(topology, "topology_profile_revision", None) != revision:
        raise ManifestError(MANIFEST_PROFILE_STALE) from None
    rows = await (
        TopologyProfileRevision.filter(
            tenant_id=authority.tenant_id,
            topology_row_id=authority.topology_id,
            revision=revision,
            deleted=False,
        )
        .using_db(connection)
        .select_for_update()
        .limit(2)
    )
    if getattr(topology, "topology_profile_revision", None) != revision:
        raise ManifestError(MANIFEST_PROFILE_STALE) from None
    if type(rows) is not list or len(rows) != 1:
        raise ManifestError(MANIFEST_PROFILE_STALE) from None
    row = rows[0]
    try:
        profile = orjson.loads(row.profile_json)
        contracts.validate_contract(profile, "aioe.network-topology.profile/v1")
        profile_hash = sha256_uri(canonicalize_json(profile))
    except (
        TypeError,
        CanonicalizationError,
        contracts.ContractError,
        orjson.JSONDecodeError,
    ):
        raise ManifestError(MANIFEST_PROFILE_STALE) from None
    if profile_hash != getattr(row, "profile_hash", None) or profile.get(
        "root_scope_seed"
    ) != getattr(topology, "root_scope_id", None):
        raise ManifestError(MANIFEST_PROFILE_STALE) from None
    return row, profile


async def _locked_resources(connection, tenant_id, resource_ids, profile):
    if not resource_ids:
        raise ManifestError(MANIFEST_INVALID_REQUEST) from None
    rows = await (
        AssetInstance.filter(
            id__in=resource_ids,
            tenant_id=tenant_id,
            deleted=False,
            status="active",
        )
        .using_db(connection)
        .select_for_update()
        .all()
    )
    by_id = {str(row.id): row for row in rows}
    if set(by_id) != set(resource_ids):
        raise ManifestError(MANIFEST_RESOURCE_NOT_FOUND) from None
    ordered = [by_id[resource_id] for resource_id in resource_ids]
    if any(_profile_excludes(row, profile) for row in ordered):
        raise ManifestError(MANIFEST_CAPABILITY_UNSUPPORTED) from None
    return ordered


def _profile_excludes(resource, profile: Mapping[str, Any]) -> bool:
    asset_type = str(getattr(resource, "asset_model_key", "") or "").strip()
    excluded = set(profile.get("excluded_asset_types", []))
    exceptions = {
        item.get("asset_type")
        for item in profile.get("explicit_asset_exceptions", [])
        if isinstance(item, dict)
    }
    return asset_type in excluded and asset_type not in exceptions


def _expected_resources(resources) -> list[dict[str, Any]]:
    expected = []
    for resource in resources:
        profile = _resolve_resource_capability(resource)
        expected.append(
            {
                "resource_id": str(resource.id),
                "capability_profile": profile["id"],
                "required_kinds": list(profile["required_kinds"]),
                "optional_kinds": list(profile["optional_kinds"]),
            }
        )
    return expected


def _validated_registry_version() -> str:
    try:
        contracts.validate_registry_integrity()
        registry = contracts.load_registry(CAPABILITY_REGISTRY_URI)
        version = registry["schema_version"]
    except (KeyError, TypeError, contracts.ContractError):
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    if not _is_text(version):
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    return version


def _resolve_resource_capability(resource) -> dict[str, Any]:
    attributes = getattr(resource, "attributes", None)
    if type(attributes) is not dict:
        raise ManifestError(MANIFEST_CAPABILITY_UNSUPPORTED) from None
    values = (
        _attribute(attributes, "device_type")
        or _canonical_text(getattr(resource, "asset_model_key", None)),
        _attribute(attributes, "vendor", "brand", "manu"),
        _attribute(attributes, "platform", "model") or "*",
        _attribute(attributes, "os_family") or "*",
    )
    try:
        return contracts.resolve_capability(*values)
    except contracts.ContractError as exc:
        if "ambiguous" in str(exc).lower():
            raise ManifestError(MANIFEST_CAPABILITY_AMBIGUOUS) from None
        raise ManifestError(MANIFEST_CAPABILITY_UNSUPPORTED) from None


def _attribute(attributes: dict, *names: str) -> str:
    for name in names:
        value = _canonical_text(attributes.get(name))
        if value:
            return value
    return ""


def _canonical_text(value: object) -> str:
    return str(value).strip().lower() if isinstance(value, str) else ""


async def _upgrade_intent(connection, authority, topology, clock):
    pair = (
        getattr(topology, "document_schema_version", None),
        getattr(topology, "generation_source", None),
    )
    if pair == (
        compatibility.V2_SCHEMA_VERSION,
        compatibility.PLAYBOOK_GENERATION_SOURCE,
    ):
        await _current_version(
            connection,
            authority,
            topology,
            {compatibility.V2_SCHEMA_VERSION},
            legacy=False,
        )
        return None, _read_now(clock)
    if pair not in {
        (None, None),
        (
            compatibility.LEGACY_SCHEMA_VERSION,
            compatibility.MANUAL_GENERATION_SOURCE,
        ),
    }:
        raise ManifestError(MANIFEST_UPGRADE_INTENT_INVALID) from None
    allowed_schemas = (
        {None} if pair == (None, None) else {compatibility.LEGACY_SCHEMA_VERSION}
    )
    version = await _current_version(
        connection,
        authority,
        topology,
        allowed_schemas,
        legacy=True,
    )
    if compatibility.is_blank_legacy_topology(topology, version):
        return None, _read_now(clock)
    return await _active_upgrade_intent(connection, authority, topology, version, clock)


async def _current_version(connection, authority, topology, allowed_schemas, *, legacy):
    version_id = getattr(topology, "current_graph_version_row_id", None)
    version = await (
        NetworkTopologyVersion.filter(
            id=version_id,
            topology_id=authority.topology_id,
            tenant_id=authority.tenant_id,
            deleted=False,
        )
        .using_db(connection)
        .select_for_update()
        .limit(2)
    )
    if type(version) is not list or len(version) != 1:
        raise ManifestError(MANIFEST_UPGRADE_INTENT_INVALID) from None
    version = version[0]
    current_revision = getattr(topology, "current_graph_revision", None)
    valid = (
        type(current_revision) is int
        and current_revision >= 0
        and getattr(version, "version", None) == current_revision
        and getattr(version, "document_schema_version", None) in allowed_schemas
        and getattr(topology, "current_graph_version_row_id", None) == version.id
        and getattr(version, "tenant_id", None) == authority.tenant_id
        and getattr(version, "topology_id", None) == authority.topology_id
    )
    if legacy:
        valid = valid and current_revision == getattr(topology, "revision", None)
    if not valid:
        raise ManifestError(MANIFEST_UPGRADE_INTENT_INVALID) from None
    return version


async def _active_upgrade_intent(connection, authority, topology, version, clock):
    active = await (
        TopologyUpgradeIntent.filter(
            tenant_id=authority.tenant_id,
            topology_row_id=authority.topology_id,
            status="ACTIVE",
            deleted=False,
        )
        .using_db(connection)
        .select_for_update()
        .limit(2)
    )
    if type(active) is not list:
        raise ManifestError(MANIFEST_UPGRADE_INTENT_INVALID) from None
    if not active:
        raise ManifestError(MANIFEST_UPGRADE_INTENT_REQUIRED) from None
    if len(active) != 1:
        raise ManifestError(MANIFEST_UPGRADE_INTENT_INVALID) from None
    intent = active[0]
    now = _read_now(clock)
    valid = (
        getattr(intent, "legacy_graph_version_row_id", None) == version.id
        and getattr(intent, "expected_legacy_revision", None) == topology.revision
        and getattr(intent, "consumed_at", None) is None
        and getattr(intent, "cancelled_at", None) is None
    )
    try:
        valid = valid and intent.expires_at > now
    except (AttributeError, TypeError):
        valid = False
    if not valid:
        raise ManifestError(MANIFEST_UPGRADE_INTENT_INVALID) from None
    return intent, now


async def _next_sequence(connection, authority, topology) -> int:
    locked_topology = await (
        NetworkTopology.filter(
            id=authority.topology_id,
            tenant_id=authority.tenant_id,
            deleted=False,
        )
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if locked_topology is None or getattr(locked_topology, "id", None) != getattr(
        topology, "id", None
    ):
        raise ManifestError(MANIFEST_TOPOLOGY_NOT_FOUND) from None
    latest = await (
        TopologyCollectionManifest.filter(
            tenant_id=authority.tenant_id,
            topology_row_id=authority.topology_id,
        )
        .using_db(connection)
        .order_by("-collection_sequence")
        .first()
    )
    if latest is None:
        return 1
    value = getattr(latest, "collection_sequence", None)
    if type(value) is not int or value < 1:
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    return value + 1


def _target_manifest(
    topology, profile, resources, sequence, manifest_id, now, registry_version
):
    issued_at = _utc_text(now)
    expires_at = _utc_text(now + timedelta(seconds=MANIFEST_TTL_SECONDS))
    target = {
        "manifest_id": manifest_id,
        "profile_revision": profile.revision,
        "collection_sequence": sequence,
        "base_graph_revision": _revision(topology, "current_graph_revision"),
        "base_publication_revision": _revision(topology, "publication_revision"),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "expected_resources": resources,
    }
    try:
        canonical = canonicalize_and_hash_manifest(target)
    except CanonicalizationError:
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    normalized = canonical.projection["target_manifest"]
    if not _is_text(registry_version):
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    return {**normalized, "manifest_hash": canonical.manifest_hash}


async def _persist_manifest(
    connection,
    authority,
    topology,
    profile,
    upgrade_intent,
    provenance,
    target,
    registry_version,
):
    await TopologyCollectionManifest.create(
        using_db=connection,
        tenant_id=authority.tenant_id,
        topology_row=topology,
        profile_revision_row=profile,
        manifest_id=target["manifest_id"],
        manifest_hash=target["manifest_hash"],
        collection_sequence=target["collection_sequence"],
        base_graph_revision=target["base_graph_revision"],
        base_publication_revision=target["base_publication_revision"],
        profile_revision=target["profile_revision"],
        selection_provenance=canonicalize_json(provenance).decode(),
        expected_resources_json=canonicalize_json(
            target["expected_resources"]
        ).decode(),
        capability_registry_version=registry_version,
        upgrade_intent_row=upgrade_intent,
        issued_at=_parse_utc(target["issued_at"]),
        expires_at=_parse_utc(target["expires_at"]),
        creator_subject=authority.subject,
        playbook_id=authority.playbook_id,
        run_id=authority.run_id,
    )


def _read_now(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    value = value.astimezone(timezone.utc)
    if value.microsecond:
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    return value


def _read_manifest_id(factory: Callable[[], str]) -> str:
    try:
        value = factory()
    except Exception:
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    if not _is_text(value) or len(value) > 255:
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    return value


def _revision(row, name: str) -> int:
    value = getattr(row, name, None)
    if type(value) is not int or value < 0:
        raise ManifestError(MANIFEST_ISSUE_FAILED) from None
    return value


def _strict_bool(row, name: str) -> bool:
    value = getattr(row, name, None)
    if type(value) is not bool:
        raise ValueError
    return value


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(f"{value[:-1]}+00:00")


def _is_text(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= 255
        and value.isprintable()
    )


class _ManifestModule(ModuleType):
    """Forward legacy module-level switch overrides to the live setting."""

    @property
    def TOPOLOGY_V2_INGRESS_ENABLED(self) -> bool:
        return settings.TOPOLOGY_V2_INGRESS_ENABLED

    @TOPOLOGY_V2_INGRESS_ENABLED.setter
    def TOPOLOGY_V2_INGRESS_ENABLED(self, value: bool) -> None:
        settings.TOPOLOGY_V2_INGRESS_ENABLED = value


sys.modules[__name__].__class__ = _ManifestModule
