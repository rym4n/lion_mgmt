"""Deterministic, evidence-backed importance classification for topology entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ImportanceReason:
    code: str
    fact_ids: tuple[str, ...]
    provenance_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImportanceDecision:
    level: str
    reasons: tuple[ImportanceReason, ...]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.reasons)


def assess_importance(
    entity_fact: Mapping[str, Any],
    facts: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> ImportanceDecision:
    """Classify one device without discarding the facts that explain the result."""
    reasons = _device_reasons(entity_fact, profile)
    reasons.extend(_related_reasons(entity_fact["id"], facts))
    if not reasons:
        reasons.append(_reason("collected_evidence", entity_fact))
    ordered = _deduplicate(reasons)
    return ImportanceDecision(_importance_level(entity_fact, ordered), tuple(ordered))


def normal_importance(fact: Mapping[str, Any]) -> ImportanceDecision:
    """Return the traceable default used for segments and external networks."""
    return ImportanceDecision("normal", (_reason("collected_evidence", fact),))


def logical_group_importance(fact: Mapping[str, Any]) -> ImportanceDecision:
    return ImportanceDecision("critical", (_reason("logical_device_group", fact),))


def _device_reasons(fact, profile):
    attributes = fact.get("attributes", {})
    reasons: list[ImportanceReason] = []
    if _explicitly_important(attributes, profile):
        reasons.append(_reason("authoritative_asset_mark", fact))
    roles = tuple(str(item).lower() for item in attributes.get("roles", ()))
    if _has_key_role(roles, profile.get("key_device_roles", ())):
        reasons.append(_reason("asset_role", fact))
    return reasons


def _explicitly_important(attributes, profile):
    if attributes.get("important") is True:
        return True
    if attributes.get("importance") in {"critical", "important"}:
        return True
    tags = set(attributes.get("tags", ()))
    return bool(tags.intersection(profile.get("important_tags", ())))


def _has_key_role(roles, configured_roles):
    configured = {str(item).lower() for item in configured_roles}
    for role in roles:
        if role in configured:
            return True
        if any(token in role for token in configured):
            return True
    return False


def _related_reasons(entity_id, facts):
    rules = (
        ("gateways", "gateway_fact", ("gateway_id", "device_id")),
        ("security_boundaries", "boundary_fact", ("device_id",)),
        ("routes", "routing_fact", ("device_id", "router_id")),
        ("routing_relationships", "routing_fact", ("local_device_id", "device_id")),
    )
    output: list[ImportanceReason] = []
    for collection, code, fields in rules:
        for fact in facts.get(collection, ()):
            if _references(fact, entity_id, fields):
                output.append(_reason(code, fact))
    output.extend(_route_owner_reasons(entity_id, facts))
    return output


def _route_owner_reasons(entity_id, facts):
    owners = {
        item["id"]: item.get("attributes", {}).get("owner_id")
        for item in facts.get("network_contexts", ())
    }
    output = []
    for fact in facts.get("routes", ()):
        context_id = fact.get("attributes", {}).get("vrf_id")
        if owners.get(context_id) == entity_id:
            output.append(_reason("routing_fact", fact))
    return output


def _references(fact, entity_id, fields):
    attributes = fact.get("attributes", {})
    return any(attributes.get(field) == entity_id for field in fields)


def _reason(code, fact):
    return ImportanceReason(
        code,
        (str(fact.get("id", "unknown")),),
        tuple(sorted(set(fact.get("provenance_ids", ())))),
    )


def _deduplicate(reasons):
    combined: dict[str, tuple[set[str], set[str]]] = {}
    for reason in reasons:
        fact_ids, provenance_ids = combined.setdefault(reason.code, (set(), set()))
        fact_ids.update(reason.fact_ids)
        provenance_ids.update(reason.provenance_ids)
    return [
        ImportanceReason(code, tuple(sorted(values[0])), tuple(sorted(values[1])))
        for code, values in sorted(combined.items())
    ]


def _importance_level(fact, reasons):
    codes = {item.code for item in reasons}
    if "authoritative_asset_mark" in codes:
        return "critical"
    roles = {str(item).lower() for item in fact.get("attributes", {}).get("roles", ())}
    if "asset_role" in codes and _has_critical_role(roles):
        return "critical"
    if codes.intersection({"asset_role", "gateway_fact", "boundary_fact", "routing_fact"}):
        return "important"
    return "normal"


def _has_critical_role(roles):
    critical_tokens = (
        "core", "edge", "firewall", "router", "load_balancer", "gateway", "perimeter"
    )
    return any(token in role for role in roles for token in critical_tokens)
