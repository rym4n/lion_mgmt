"""Evidence closure and minimum relation-evidence rules for formal Facts."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

import orjson

from services.network_topology.contracts import ContractError, validate_contract
from services.network_topology.evidence_locator import LocatorError, resolve_evidence


class EvidenceValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_relation_evidence(
    relation_type: str, evidence: dict[str, Any]
) -> dict[str, Any]:
    """Require explicit evidence for each relation family; never infer a winner."""
    validators = {
        "physical": _physical,
        "l2": _l2,
        "l3": _l3,
        "routing": _routing,
        "tunnel": _tunnel,
        "gateway": _gateway,
        "security": _security,
        "wan": _wan,
    }
    try:
        validators[relation_type](evidence)
    except KeyError as exc:
        raise EvidenceValidationError("UNKNOWN_RELATION_TYPE") from exc
    return evidence


def _physical(value: dict[str, Any]) -> None:
    if value.get("protocol") not in {"lldp", "cdp"}:
        raise EvidenceValidationError("PHYSICAL_NEIGHBOR_EVIDENCE_REQUIRED")
    _require(value, ("local_port", "remote_port"), "PHYSICAL_NEIGHBOR_EVIDENCE_REQUIRED")


def _l2(value: dict[str, Any]) -> None:
    if not value.get("local_trunk") or not value.get("remote_trunk"):
        raise EvidenceValidationError("BIDIRECTIONAL_L2_EVIDENCE_REQUIRED")
    if value.get("local_vlan") != value.get("remote_vlan"):
        raise EvidenceValidationError("L2_EVIDENCE_CONFLICT")


def _l3(value: dict[str, Any]) -> None:
    if value.get("protocol") not in {"arp", "nd", "l3_neighbor"}:
        raise EvidenceValidationError("L3_NEIGHBOR_EVIDENCE_REQUIRED")
    _require(value, ("local_address", "remote_address", "context"),
             "L3_NEIGHBOR_EVIDENCE_REQUIRED")


def _routing(value: dict[str, Any]) -> None:
    _require(value, ("protocol", "session_state"), "ROUTING_NEIGHBOR_EVIDENCE_REQUIRED")
    if str(value["session_state"]).lower() not in {"up", "established", "full"}:
        raise EvidenceValidationError("ROUTING_SESSION_NOT_OPERATIONAL")


def _tunnel(value: dict[str, Any]) -> None:
    _require(value, ("local_endpoint", "remote_endpoint"), "TUNNEL_ENDPOINT_EVIDENCE_REQUIRED")
    if value.get("local_context") != value.get("remote_context"):
        raise EvidenceValidationError("TUNNEL_CONTEXT_MISMATCH")


def _gateway(value: dict[str, Any]) -> None:
    observed = value.get("fhrp_protocol") and value.get("virtual_address")
    authoritative = value.get("authority_validated") and value.get("source_revision")
    if not observed and not authoritative:
        raise EvidenceValidationError("GATEWAY_EVIDENCE_REQUIRED")


def _security(value: dict[str, Any]) -> None:
    if any(key in value for key in ("allowed_zone", "policy", "permits")):
        raise EvidenceValidationError("SECURITY_POLICY_INFERENCE_FORBIDDEN")
    _require(value, ("device_id", "interface_id", "zone_id"),
             "SECURITY_ZONE_BINDING_REQUIRED")


def _wan(value: dict[str, Any]) -> None:
    if value.get("connection_type") == "circuit":
        _require(value, ("circuit_id", "source_revision"),
                 "AUTHORITATIVE_CIRCUIT_REQUIRED")
        if not value.get("authority_validated"):
            raise EvidenceValidationError("AUTHORITATIVE_CIRCUIT_REQUIRED")
        return
    _require(value, ("connection_type", "local_endpoint", "remote_endpoint"),
             "WAN_PEER_EVIDENCE_REQUIRED")


def _require(value: dict[str, Any], fields: tuple[str, ...], code: str) -> None:
    if any(value.get(field) in (None, "") for field in fields):
        raise EvidenceValidationError(code)


def bind_observed_evidence(
    evidence_refs: list[dict[str, Any]],
    observations: tuple[dict[str, Any], ...],
    resource_id: str,
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Resolve locators in this exact redacted chunk and bind server provenance."""
    by_id = {item.get("observation_id"): item for item in observations}
    catalog: list[dict[str, Any]] = []
    resolved: list[Any] = []
    for index, reference in enumerate(evidence_refs):
        observation = by_id.get(reference.get("observation_id"))
        if observation is None:
            raise EvidenceValidationError("EVIDENCE_OBSERVATION_OUTSIDE_CHUNK")
        if observation.get("resource_id", resource_id) != resource_id:
            raise EvidenceValidationError("EVIDENCE_RESOURCE_MISMATCH")
        resolved.append(_resolve(observation, reference["locator"]))
        catalog.append(_observed_entry(observation, reference["locator"], resource_id, index))
    return catalog, resolved


def _resolve(observation: dict[str, Any], locator: dict[str, Any]) -> Any:
    try:
        return resolve_evidence(observation.get("data"), locator)
    except LocatorError as exc:
        raise EvidenceValidationError("EVIDENCE_LOCATOR_INVALID") from exc


def _observed_entry(observation, locator, resource_id, index):
    del index
    redacted_hash = observation.get("content_sha256") or observation.get("redacted_sha256")
    provenance = {
        "type": "observed", "resource_id": resource_id,
        "observation_id": observation["observation_id"],
        "redacted_sha256": redacted_hash, "locator": deepcopy(locator),
    }
    try:
        validate_contract(provenance, "aioe.network-topology.provenance/v1")
    except ContractError as exc:
        raise EvidenceValidationError("EVIDENCE_PROVENANCE_INVALID") from exc
    digest = hashlib.sha256(orjson.dumps(locator, option=orjson.OPT_SORT_KEYS)).hexdigest()[:16]
    return {"id": f"observed:{resource_id}:{observation['observation_id']}:{digest}",
            "provenance": provenance}


def validate_authoritative_provenance(provenance: dict[str, Any]) -> dict[str, Any]:
    if provenance.get("type") != "authoritative":
        raise EvidenceValidationError("AUTHORITATIVE_CONTEXT_REQUIRED")
    try:
        validate_contract(provenance, "aioe.network-topology.provenance/v1")
    except ContractError as exc:
        raise EvidenceValidationError("AUTHORITATIVE_PROVENANCE_INVALID") from exc
    return deepcopy(provenance)


def derived_provenance(
    rule_id: str, profile_revision: int, member_ids: list[str]
) -> dict[str, Any]:
    value = {"type": "derived", "rule_id": rule_id,
             "profile_revision": profile_revision,
             "member_ids": sorted(set(member_ids))}
    try:
        validate_contract(value, "aioe.network-topology.provenance/v1")
    except ContractError as exc:
        raise EvidenceValidationError("DERIVED_PROVENANCE_INVALID") from exc
    return value


def keep_asset_device(
    device_type: str,
    *,
    authoritative_important: bool = False,
    profile_allows_terminal: bool = False,
) -> bool:
    if device_type.lower() not in {"host", "terminal", "endpoint"}:
        return True
    return authoritative_important and profile_allows_terminal
