"""Stable identity resolution for formal topology Facts.

The resolver is intentionally independent from graph projection.  Callers may
hydrate it from the identity tables, resolve one facts/v1 document, then persist
the returned immutable records and audit events in their publication transaction.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from hashlib import sha256
from ipaddress import ip_address
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.parse import quote


FACTS_SCHEMA = "aioe.network-topology.facts/v1"
STRONG_ALIAS_TYPES = frozenset({"serial", "chassis"})
LOGICAL_GROUP_TYPES = frozenset({"ha", "stack", "irf", "vss", "vsf", "mlag", "cluster"})
TRANSITION_TYPES = frozenset({"merge", "split", "replacement"})


class IdentityResolutionError(ValueError):
    """Raised when formal Facts cannot be safely interpreted."""


class IdentityTransitionError(ValueError):
    """Raised when a requested identity transition is structurally invalid."""


@dataclass(frozen=True, slots=True)
class IdentityAlias:
    alias_type: str
    namespace: str
    value: str
    subtype: str | None = None

    @property
    def key(self) -> str:
        normalized = _comparison_value(self.alias_type, self.value)
        return "\x1f".join((self.alias_type, self.namespace, normalized))

    @property
    def value_hash(self) -> str:
        return f"sha256:{sha256(self.key.encode()).hexdigest()}"

    @property
    def strong(self) -> bool:
        return self.alias_type in STRONG_ALIAS_TYPES


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    tenant_id: str
    canonical_id: str
    entity_kind: str
    canonical_fingerprint: str
    aliases: tuple[IdentityAlias, ...] = ()


@dataclass(frozen=True, slots=True)
class UnresolvedIdentity:
    reason_code: str
    selector: Mapping[str, Any]
    candidate_matches: tuple[str, ...]
    fact_id: str


@dataclass(frozen=True, slots=True)
class IdentityAudit:
    operation: str
    status: str
    source_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    actor: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LogicalDeviceGroup:
    canonical_id: str
    group_type: str
    identity: str
    physical_member_ids: tuple[str, ...]
    provenance_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    identities: tuple[IdentityRecord, ...]
    groups: tuple[LogicalDeviceGroup, ...]
    unresolved: tuple[UnresolvedIdentity, ...]
    audits: tuple[IdentityAudit, ...]
    fact_identity_map: Mapping[str, str]


class IdentityPersistence(Protocol):
    """Persistence boundary used by a publishing transaction."""

    async def load_identities(self, tenant_id: str) -> Iterable[IdentityRecord]: ...

    async def save_resolution(
        self, tenant_id: str, resolution: IdentityResolution
    ) -> None: ...


PresentationMigrator = Callable[[str, str, IdentityAudit], None]


def normalize_alias(raw: Mapping[str, Any], *, vendor: str) -> IdentityAlias:
    """Apply collision-safe namespaces to a facts/v1 identity alias."""
    alias_type = _required_text(raw, "type").lower()
    source_namespace = _required_text(raw, "namespace")
    value = _required_text(raw, "value")
    subtype = _optional_text(raw.get("subtype"))
    normalized_vendor = vendor.strip().lower() or "unknown"
    namespace = _alias_namespace(
        alias_type, source_namespace, normalized_vendor, subtype
    )
    return IdentityAlias(alias_type, namespace, _alias_value(alias_type, value), subtype)


class IdentityRegistry:
    """Tenant-local, stateful identity resolver with immutable canonical IDs."""

    def __init__(
        self,
        tenant_id: str,
        source_namespace: str,
        *,
        existing: Iterable[IdentityRecord] = (),
        presentation_migrator: PresentationMigrator | None = None,
    ) -> None:
        self.tenant_id = _nonempty(tenant_id, "tenant_id")
        self.source_namespace = _nonempty(source_namespace, "source_namespace")
        self._records = {item.canonical_id: item for item in existing}
        self._alias_owners: dict[str, set[str]] = defaultdict(set)
        self._audits: list[IdentityAudit] = []
        self._presentation_migrator = presentation_migrator
        self._index_existing()

    @property
    def identities(self) -> tuple[IdentityRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    @property
    def audits(self) -> tuple[IdentityAudit, ...]:
        return tuple(self._audits)

    def resolve_facts(self, facts: Mapping[str, Any]) -> IdentityResolution:
        _validate_facts_document(facts)
        devices = sorted(facts["devices"], key=lambda item: item["id"])
        prepared = {item["id"]: self._device_aliases(item) for item in devices}
        duplicate_strong = _duplicate_strong_aliases(devices, prepared)
        unresolved: list[UnresolvedIdentity] = []
        new_audits: list[IdentityAudit] = []
        fact_map: dict[str, str] = {}
        resolved_ids: set[str] = set()
        for device in devices:
            record = self._resolve_device(
                device, prepared[device["id"]], duplicate_strong,
                unresolved, new_audits,
            )
            if record is not None:
                fact_map[device["id"]] = record.canonical_id
                resolved_ids.add(record.canonical_id)
        groups = self._resolve_groups(facts["device_groups"], fact_map, unresolved)
        identities = tuple(self._records[item] for item in sorted(resolved_ids))
        return IdentityResolution(
            identities, groups, tuple(unresolved), tuple(new_audits), fact_map
        )

    def record_transition(
        self,
        operation: str,
        source_ids: tuple[str, ...],
        target_ids: tuple[str, ...],
        *,
        actor: str,
        details: Mapping[str, Any] | None = None,
    ) -> IdentityAudit:
        _validate_transition(operation, source_ids, target_ids)
        audit = IdentityAudit(
            operation, "RECORDED", source_ids, target_ids,
            _nonempty(actor, "actor"), dict(details or {}),
        )
        self._audits.append(audit)
        if operation == "merge" and self._presentation_migrator is not None:
            self._presentation_migrator(source_ids[0], target_ids[0], audit)
        return audit

    def _index_existing(self) -> None:
        for canonical_id, record in self._records.items():
            if record.tenant_id != self.tenant_id:
                raise IdentityResolutionError("existing identity belongs to another tenant")
            for alias in record.aliases:
                self._alias_owners[alias.key].add(canonical_id)

    def _device_aliases(self, device: Mapping[str, Any]) -> tuple[IdentityAlias, ...]:
        attributes = device.get("attributes", {})
        vendor = str(attributes.get("vendor", "unknown"))
        aliases = [normalize_alias(item, vendor=vendor)
                   for item in attributes.get("identity_aliases", ())]
        explicit_ips = {item.value for item in aliases if item.alias_type == "management_ip"}
        for value in attributes.get("management_ips", ()):
            if str(value) not in explicit_ips:
                aliases.append(normalize_alias(
                    {"type": "management_ip", "namespace": "default/default",
                     "value": value}, vendor=vendor
                ))
        by_key = {item.key: item for item in aliases}
        return tuple(by_key[key] for key in sorted(by_key))

    def _resolve_device(
        self,
        device: Mapping[str, Any],
        aliases: tuple[IdentityAlias, ...],
        duplicate_strong: set[str],
        unresolved: list[UnresolvedIdentity],
        audits: list[IdentityAudit],
    ) -> IdentityRecord | None:
        state = device["attributes"].get("inventory_state")
        if state == "managed":
            canonical_id = self._managed_id(device)
        else:
            canonical_id = self._unmanaged_id(
                device, aliases, duplicate_strong, unresolved
            )
            if canonical_id is None:
                return None
        record = self._ensure_record(canonical_id)
        return self._attach_aliases(record, aliases, device["id"], unresolved, audits)

    def _managed_id(self, device: Mapping[str, Any]) -> str:
        attributes = device["attributes"]
        asset_id = attributes.get("asset_id") or _fact_local_id(device["id"], "device")
        if not asset_id:
            resources = device.get("resource_ids", ())
            asset_id = resources[0] if len(resources) == 1 else None
        if not asset_id:
            raise IdentityResolutionError("managed device needs one resource or asset id")
        return _canonical_id("device", self.source_namespace, str(asset_id))

    def _unmanaged_id(
        self,
        device: Mapping[str, Any],
        aliases: tuple[IdentityAlias, ...],
        duplicate_strong: set[str],
        unresolved: list[UnresolvedIdentity],
    ) -> str | None:
        strong = tuple(item for item in aliases if item.strong)
        if not strong:
            unresolved.append(_unresolved("WEAK_IDENTITY_EVIDENCE", device, aliases, ()))
            return None
        if any(item.key in duplicate_strong for item in strong):
            unresolved.append(_unresolved("ALIAS_CONFLICT", device, strong, ()))
            return None
        owners = {owner for alias in strong for owner in self._alias_owners[alias.key]}
        if len(owners) > 1:
            unresolved.append(_unresolved("ALIAS_CONFLICT", device, strong, owners))
            return None
        if owners:
            return next(iter(owners))
        fingerprint = _digest("\n".join(sorted(item.key for item in strong)))[:24]
        return _canonical_id("device", self.source_namespace, f"discovered-{fingerprint}")

    def _ensure_record(self, canonical_id: str) -> IdentityRecord:
        record = self._records.get(canonical_id)
        if record is None:
            record = IdentityRecord(
                self.tenant_id, canonical_id, "device", _fingerprint(canonical_id)
            )
            self._records[canonical_id] = record
        return record

    def _attach_aliases(
        self,
        record: IdentityRecord,
        aliases: tuple[IdentityAlias, ...],
        fact_id: str,
        unresolved: list[UnresolvedIdentity],
        audits: list[IdentityAudit],
    ) -> IdentityRecord:
        accepted = {item.key: item for item in record.aliases}
        for alias in aliases:
            incumbents = self._alias_owners[alias.key] - {record.canonical_id}
            if incumbents:
                event = self._alias_conflict(record, alias, fact_id, incumbents)
                unresolved.append(event[0])
                audits.append(event[1])
                continue
            accepted[alias.key] = alias
            self._alias_owners[alias.key].add(record.canonical_id)
        updated = replace(record, aliases=tuple(accepted[key] for key in sorted(accepted)))
        self._records[record.canonical_id] = updated
        return updated

    def _alias_conflict(
        self, record: IdentityRecord, alias: IdentityAlias,
        fact_id: str, incumbents: set[str],
    ) -> tuple[UnresolvedIdentity, IdentityAudit]:
        candidates = tuple(sorted(incumbents | {record.canonical_id}))
        unresolved = UnresolvedIdentity(
            "ALIAS_CONFLICT", _selector((alias,)), candidates, fact_id
        )
        audit = IdentityAudit(
            "alias_conflict", "UNRESOLVED", tuple(sorted(incumbents)),
            (record.canonical_id,), "identity-registry",
            {"alias_type": alias.alias_type, "alias_namespace": alias.namespace,
             "alias_value_hash": alias.value_hash},
        )
        self._audits.append(audit)
        return unresolved, audit

    def _resolve_groups(
        self, groups: Iterable[Mapping[str, Any]], fact_map: Mapping[str, str],
        unresolved: list[UnresolvedIdentity],
    ) -> tuple[LogicalDeviceGroup, ...]:
        output = []
        for fact in sorted(groups, key=lambda item: item["id"]):
            group = _logical_group(fact, self.source_namespace, fact_map)
            if group is None:
                unresolved.append(UnresolvedIdentity(
                    "GROUP_MEMBER_UNRESOLVED",
                    {"member_ids": tuple(fact["attributes"].get("member_ids", ()))},
                    (), fact["id"],
                ))
            else:
                output.append(group)
        return tuple(output)


async def resolve_with_persistence(
    tenant_id: str,
    source_namespace: str,
    facts: Mapping[str, Any],
    persistence: IdentityPersistence,
    *,
    presentation_migrator: PresentationMigrator | None = None,
) -> IdentityResolution:
    """Resolve and persist through the caller's transaction-aware adapter."""
    existing = await persistence.load_identities(tenant_id)
    registry = IdentityRegistry(
        tenant_id, source_namespace, existing=existing,
        presentation_migrator=presentation_migrator,
    )
    resolution = registry.resolve_facts(facts)
    await persistence.save_resolution(tenant_id, resolution)
    return resolution


def _logical_group(fact, namespace, fact_map):
    attributes = fact.get("attributes", {})
    group_type = attributes.get("group_type")
    if group_type not in LOGICAL_GROUP_TYPES:
        raise IdentityResolutionError("unsupported logical device group type")
    member_facts = tuple(attributes.get("member_ids", ()))
    if not member_facts or any(item not in fact_map for item in member_facts):
        return None
    local_id = _fact_local_id(fact["id"], "device-group")
    return LogicalDeviceGroup(
        _canonical_id("device-group", namespace, local_id), group_type,
        _nonempty(str(attributes.get("identity", "")), "group identity"),
        tuple(fact_map[item] for item in member_facts),
        tuple(fact.get("provenance_ids", ())),
    )


def _duplicate_strong_aliases(devices, prepared):
    keys = [alias.key for device in devices
            if device["attributes"].get("inventory_state") != "managed"
            for alias in prepared[device["id"]] if alias.strong]
    return {key for key, count in Counter(keys).items() if count > 1}


def _alias_namespace(alias_type, namespace, vendor, subtype):
    if alias_type == "serial":
        return f"vendor={vendor};source={namespace}"
    if alias_type == "chassis":
        return f"vendor={vendor};subtype={subtype or 'unspecified'};context={namespace}"
    if alias_type == "management_ip":
        domain, vrf = _management_context(namespace)
        return f"management-domain={domain};vrf={vrf}"
    if alias_type == "hostname":
        return f"naming-domain={namespace}"
    raise IdentityResolutionError(f"unsupported identity alias type: {alias_type}")


def _management_context(namespace):
    if "/" not in namespace:
        return namespace, "default"
    domain, vrf = namespace.split("/", 1)
    if not domain or not vrf:
        raise IdentityResolutionError("management IP namespace needs domain/VRF")
    return domain, vrf


def _alias_value(alias_type, value):
    if alias_type == "management_ip":
        try:
            return str(ip_address(value))
        except ValueError as exc:
            raise IdentityResolutionError("invalid management IP alias") from exc
    return value.strip()


def _comparison_value(alias_type, value):
    if alias_type in {"serial", "chassis", "hostname"}:
        return value.casefold()
    return value


def _unresolved(reason, device, aliases, candidates):
    return UnresolvedIdentity(
        reason, _selector(aliases), tuple(sorted(candidates)), device["id"]
    )


def _selector(aliases):
    return {"aliases": tuple(
        {"type": item.alias_type, "namespace": item.namespace,
         "value_hash": item.value_hash} for item in aliases
    )}


def _validate_facts_document(facts):
    if facts.get("schema_version") != FACTS_SCHEMA:
        raise IdentityResolutionError("identity registry only accepts formal facts/v1")
    if not isinstance(facts.get("devices"), list):
        raise IdentityResolutionError("facts/v1 devices must be an array")
    if not isinstance(facts.get("device_groups"), list):
        raise IdentityResolutionError("facts/v1 device_groups must be an array")


def _validate_transition(operation, sources, targets):
    if operation not in TRANSITION_TYPES:
        raise IdentityTransitionError("unsupported identity transition")
    valid = {
        "merge": len(sources) == 1 and len(targets) == 1,
        "replacement": len(sources) == 1 and len(targets) == 1,
        "split": len(sources) == 1 and len(targets) >= 2,
    }
    if not sources or not targets or not valid[operation] or set(sources) & set(targets):
        raise IdentityTransitionError("invalid identity transition cardinality")


def _canonical_id(kind, namespace, local_id):
    safe = lambda item: quote(_nonempty(item, kind), safe="-._~")
    return f"{kind}:{safe(namespace)}:{safe(local_id)}"


def _fact_local_id(identifier, prefix):
    marker = f"{prefix}:"
    if not isinstance(identifier, str) or not identifier.startswith(marker):
        raise IdentityResolutionError(f"invalid {prefix} fact id")
    return identifier[len(marker):]


def _fingerprint(canonical_id):
    return f"sha256:{_digest(canonical_id)}"


def _digest(value):
    return sha256(value.encode()).hexdigest()


def _required_text(value, key):
    if key not in value:
        raise IdentityResolutionError(f"identity alias missing {key}")
    return _nonempty(str(value[key]), key)


def _optional_text(value):
    return None if value is None else _nonempty(str(value), "subtype")


def _nonempty(value, field_name):
    normalized = str(value).strip()
    if not normalized:
        raise IdentityResolutionError(f"{field_name} must not be empty")
    return normalized
