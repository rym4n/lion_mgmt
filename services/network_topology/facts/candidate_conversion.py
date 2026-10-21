"""Convert LLM candidate documents into server-bound formal Facts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from ipaddress import ip_network
from typing import Any

from services.network_topology.parsers.base import CandidateBatch
from services.network_topology.contracts import ContractError, validate_contract

from .validation import (
    EvidenceValidationError,
    bind_observed_evidence,
    keep_asset_device,
)


CANDIDATE_SCHEMA = "aioe.network-topology.llm-fact-candidates/v1"
FACTS_SCHEMA = "aioe.network-topology.facts/v1"
FACT_ARRAYS = (
    "devices", "device_groups", "network_contexts", "interfaces", "segments",
    "adjacencies", "routes", "routing_relationships", "tunnels", "memberships",
    "gateways", "security_boundaries", "wan_connections",
)
PREFIXES = {
    "devices": "device", "device_groups": "device-group",
    "network_contexts": "context", "interfaces": "interface", "segments": "network",
    "adjacencies": "neighbor", "routes": "route",
    "routing_relationships": "routing", "tunnels": "tunnel",
    "memberships": "membership", "gateways": "gateway",
    "security_boundaries": "security", "wan_connections": "wan",
}


class CandidateConversionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ConversionResult:
    facts: dict[str, Any]
    resource_id: str
    chunk_id: str
    input_hash: str
    collector_run_id: str | None = None
    llm_summary: dict[str, Any] | None = None
    validation_items: tuple[dict[str, Any], ...] = ()


def convert_candidates(
    document: dict[str, Any],
    *,
    snapshot_id: str,
    analysis_run_id: str,
    resource_id: str,
    chunk_id: str,
    input_hash: str,
    observations: tuple[dict[str, Any], ...],
    collector_run_id: str | None = None,
    llm_summary: dict[str, Any] | None = None,
    authoritative_important: bool = False,
    profile_allows_terminal: bool = False,
) -> ConversionResult:
    _validate_input(document, input_hash)
    mapping = _canonical_mapping(document, chunk_id, resource_id)
    facts = _empty_facts(snapshot_id, analysis_run_id)
    validation: list[dict[str, Any]] = []
    for family in FACT_ARRAYS:
        for candidate in document[family]:
            if family == "devices" and not keep_asset_device(
                candidate["device_type"],
                authoritative_important=authoritative_important,
                profile_allows_terminal=profile_allows_terminal,
            ):
                validation.append({"code": "TERMINAL_EXCLUDED", "candidate_id": candidate["candidate_id"]})
                continue
            fact, provenance = _convert_one(
                family, candidate, mapping, resource_id, observations
            )
            facts[family].append(fact)
            facts["provenance_catalog"].extend(provenance)
            if fact["parsing_state"] == "unresolved":
                validation.append({"code": "RELATION_EVIDENCE_UNRESOLVED",
                                   "fact_id": fact["id"], "family": family})
    unresolved, unresolved_provenance = _convert_unresolved(
        document["unresolved_references"], mapping, resource_id, observations
    )
    facts["unresolved_references"] = unresolved
    facts["provenance_catalog"].extend(unresolved_provenance)
    facts["provenance_catalog"] = _unique_provenance(facts["provenance_catalog"])
    try:
        validate_contract(facts, FACTS_SCHEMA)
    except ContractError as exc:
        raise CandidateConversionError("formal facts schema validation failed") from exc
    return ConversionResult(
        facts=facts, resource_id=resource_id, chunk_id=chunk_id,
        input_hash=input_hash, collector_run_id=collector_run_id,
        llm_summary=_llm_summary(llm_summary), validation_items=tuple(validation),
    )


def convert_candidate_batch(
    batch: CandidateBatch,
    *,
    snapshot_id: str,
    analysis_run_id: str,
    resource_id: str,
    chunk_id: str,
    input_hash: str,
    observations: tuple[dict[str, Any], ...],
    authoritative_context: dict[str, Any] | None = None,
    collector_run_id: str | None = None,
) -> ConversionResult:
    """Bind facts-shaped output from a trusted registered parser."""
    _validate_batch_binding(batch, resource_id, chunk_id, input_hash)
    facts = _empty_facts(snapshot_id, analysis_run_id)
    for family, values in batch.candidates.items():
        if family not in facts:
            raise CandidateConversionError("registered parser returned unknown fact family")
        facts[family].extend(deepcopy(list(values)))
    _validate_batch_provenance(
        facts, observations, resource_id, authoritative_context or {}
    )
    try:
        validate_contract(facts, FACTS_SCHEMA)
    except ContractError as exc:
        raise CandidateConversionError("registered parser facts schema validation failed") from exc
    return ConversionResult(
        facts=facts, resource_id=resource_id, chunk_id=chunk_id,
        input_hash=input_hash, collector_run_id=collector_run_id,
    )


def _llm_summary(value):
    if value is None:
        return None
    allowed = {"provider", "model", "prompt_version", "prompt_tokens",
               "completion_tokens", "total_tokens", "attempts", "elapsed_ms"}
    if not isinstance(value, dict) or set(value) != allowed:
        raise CandidateConversionError("LLM audit summary is invalid")
    if any(not isinstance(value[key], str) or not value[key]
           for key in ("provider", "model", "prompt_version")):
        raise CandidateConversionError("LLM audit summary is invalid")
    if any(type(value[key]) is not int or value[key] < 0 for key in allowed - {
        "provider", "model", "prompt_version"
    }):
        raise CandidateConversionError("LLM audit summary is invalid")
    return deepcopy(value)


def _validate_batch_binding(batch, resource_id, chunk_id, input_hash):
    if (batch.resource_id, batch.chunk_id, batch.input_hash) != (
        resource_id, chunk_id, input_hash
    ):
        raise CandidateConversionError("registered parser binding mismatch")


def _validate_batch_provenance(facts, observations, resource_id, authority):
    by_id = {item.get("observation_id"): item for item in observations}
    for entry in facts["provenance_catalog"]:
        provenance = entry.get("provenance", {})
        if provenance.get("type") == "authoritative":
            _validate_provenance_contract(provenance, "authoritative")
            if not _authority_contains(authority, provenance):
                raise CandidateConversionError("authoritative provenance is outside context")
            continue
        if provenance.get("type") == "derived":
            _validate_provenance_contract(provenance, "derived")
            if provenance["member_ids"] != sorted(provenance["member_ids"]):
                raise CandidateConversionError("derived provenance members are not stable")
            continue
        _validate_batch_observed(provenance, by_id, resource_id)


def _validate_batch_observed(provenance, observations, resource_id):
    observation = observations.get(provenance.get("observation_id"))
    expected_hash = None if observation is None else (
        observation.get("content_sha256") or observation.get("redacted_sha256")
    )
    if (observation is None or provenance.get("resource_id") != resource_id
            or provenance.get("redacted_sha256") != expected_hash):
        raise CandidateConversionError("registered parser observed provenance mismatch")
    try:
        bind_observed_evidence([{"observation_id": provenance["observation_id"],
                                "locator": provenance["locator"]}],
                               tuple(observations.values()), resource_id)
    except EvidenceValidationError as exc:
        raise CandidateConversionError("registered parser evidence invalid") from exc


def _validate_provenance_contract(provenance, provenance_type):
    if provenance.get("type") != provenance_type:
        raise CandidateConversionError("provenance type mismatch")
    try:
        validate_contract(provenance, "aioe.network-topology.provenance/v1")
    except ContractError as exc:
        raise CandidateConversionError(f"{provenance_type} provenance invalid") from exc


def _authority_contains(context, provenance):
    expected = {key: value for key, value in provenance.items() if key != "type"}
    for records in context.values():
        if not isinstance(records, list):
            continue
        for record in records:
            value = record.get("provenance") if isinstance(record, dict) else None
            if value == provenance or value == expected:
                return True
    return False


def _validate_input(document: dict[str, Any], input_hash: str) -> None:
    try:
        validate_contract(document, CANDIDATE_SCHEMA)
    except ContractError as exc:
        raise CandidateConversionError("candidate schema validation failed") from exc
    if not isinstance(input_hash, str) or not input_hash.startswith("sha256:"):
        raise CandidateConversionError("candidate input hash binding is invalid")


def _empty_facts(snapshot_id: str, analysis_run_id: str) -> dict[str, Any]:
    return {
        "schema_version": FACTS_SCHEMA, "snapshot_id": snapshot_id,
        "analysis_run_id": analysis_run_id,
        **{family: [] for family in FACT_ARRAYS},
        "unresolved_references": [], "provenance_catalog": [],
    }


def _canonical_mapping(document, chunk_id, resource_id):
    candidates = [(family, item) for family in FACT_ARRAYS for item in document[family]]
    if len(document["devices"]) > 1:
        raise CandidateConversionError("one chunk cannot define multiple canonical devices")
    mapping = {}
    for family, item in candidates:
        local_id = item["candidate_id"]
        if not local_id.startswith(f"{chunk_id}:") or local_id in mapping:
            raise CandidateConversionError("candidate IDs must be unique and chunk namespaced")
        mapping[local_id] = _canonical_id(family, local_id, resource_id)
    return mapping


def _canonical_id(family: str, local_id: str, resource_id: str) -> str:
    if family == "devices":
        return f"device:{resource_id}"
    digest = hashlib.sha256(f"{resource_id}\0{family}\0{local_id}".encode()).hexdigest()[:20]
    return f"{PREFIXES[family]}:{resource_id}:{digest}"


def _convert_one(family, candidate, mapping, resource_id, observations):
    try:
        provenance, resolved = bind_observed_evidence(
            candidate["evidence_refs"], observations, resource_id
        )
    except EvidenceValidationError as exc:
        raise CandidateConversionError(f"candidate observation evidence invalid: {exc.code}") from exc
    _require_semantic_support(family, candidate, resolved)
    provenance_ids = [item["id"] for item in provenance]
    fact = {
        "id": mapping[candidate["candidate_id"]],
        "fact_family": _fact_family(family),
        "resource_ids": [resource_id],
        "source_candidate_ids": [candidate["candidate_id"]],
        "attributes": _attributes(family, candidate, mapping, resource_id, observations),
        "provenance_ids": provenance_ids,
        "parsing_state": _parsing_state(family, candidate),
    }
    return fact, provenance


def _fact_family(family: str) -> str:
    return {
        "devices": "device", "device_groups": "device_group",
        "network_contexts": "network_context", "interfaces": "interface",
        "segments": "network", "adjacencies": "neighbor", "routes": "route",
        "routing_relationships": "routing_relationship", "tunnels": "tunnel",
        "memberships": "membership", "gateways": "gateway",
        "security_boundaries": "security", "wan_connections": "wan",
    }[family]


def _attributes(family, item, mapping, resource_id, observations):
    functions = {
        "devices": _device_attributes, "device_groups": _group_attributes,
        "network_contexts": _context_attributes, "interfaces": _interface_attributes,
        "segments": _segment_attributes, "adjacencies": _adjacency_attributes,
        "routes": _route_attributes, "routing_relationships": _routing_attributes,
        "tunnels": _tunnel_attributes, "memberships": _membership_attributes,
        "gateways": _gateway_attributes, "security_boundaries": _security_attributes,
        "wan_connections": _wan_attributes,
    }
    return functions[family](item, mapping, resource_id, observations)


def _device_attributes(item, *_):
    return {key: deepcopy(item[key]) for key in (
        "device_type", "identity_aliases", "roles", "inventory_state"
    )}


def _group_attributes(item, mapping, *_):
    return {"group_type": item["group_type"], "identity": item["identity"],
            "member_ids": sorted(_selector(value, mapping)
                                 for value in item["member_selectors"])}


def _context_attributes(item, mapping, *_):
    return {"owner_id": _reference(item["owning_candidate_ref"], mapping),
            "local_id": item["local_id"], "name": item["name"],
            "context_type": item["kind"]}


def _interface_attributes(item, mapping, *_):
    return {
        "device_id": _reference(item["device_candidate_ref"], mapping),
        "context_id": _optional_reference(item["context_candidate_ref"], mapping),
        "canonical_name": item["canonical_name"], "raw_name": item["raw_name"],
        "if_index": item["if_index"], "mac": item["mac"],
        "parent_interface_id": _optional_reference(item["parent_ref"], mapping),
        "lag_id": _optional_reference(item["lag_ref"], mapping),
        "addresses": sorted(set(item["addresses"])),
        "configured_state": item["configured_state"],
        "operational_state": item["operational_state"],
    }


def _segment_attributes(item, mapping, resource_id, *_):
    return {"canonical_cidr": item["canonical_cidr"],
            "network_domain_id": f"resource:{resource_id}",
            "device_id": _optional_reference(item["device_candidate_ref"], mapping),
            "context_id": _optional_reference(item["context_candidate_ref"], mapping),
            "vrf_id": None, "vlan_id": item["vlan"], "vni": item["vni"],
            "gateway_ids": sorted(_selector(value, mapping)
                                  for value in item["gateway_selectors"])}


def _adjacency_attributes(item, mapping, *_):
    return {"neighbor_type": item["kind"],
            "local_id": _reference(item["local_candidate_ref"], mapping),
            "remote_id": _selector(item["remote_selector"], mapping),
            "attachments": sorted(set(item["attachments"])), "layer": item["layer"],
            "context_id": _optional_reference(item["context_ref"], mapping),
            "state": item["state"] or "unknown"}


def _route_attributes(item, mapping, *_):
    selector = item["next_hop_selector"]
    return {"route_type": item["route_type"], "prefix": item["prefix"],
            "vrf_id": item["vrf"],
            "next_hop_id": _selector(selector, mapping) if selector else None,
            "interface_id": _optional_reference(item["interface_candidate_ref"], mapping),
            "state": item["state"]}


def _routing_attributes(item, mapping, resource_id, *_):
    return {"protocol": item["protocol"],
            "local_id": _selector(item["local_selector"], mapping),
            "remote_id": _selector(item["remote_selector"], mapping),
            "vrf_id": item["vrf"], "session_state": item["session_state"]}


def _tunnel_attributes(item, mapping, *_):
    return {"tunnel_type": item["tunnel_type"], "tunnel_id": item["tunnel_id"],
            "context_id": _optional_reference(item["context_ref"], mapping),
            "interface_id": None, "local_endpoint": item["local_endpoint"],
            "remote_endpoint": item["remote_endpoint"], "protected_prefixes": [],
            "state": item["state"] or "unknown"}


def _membership_attributes(item, mapping, *_):
    return {"member_id": _reference(item["member_candidate_ref"], mapping),
            "container_id": _reference(item["container_candidate_ref"], mapping),
            "membership_kind": item["membership_kind"]}


def _gateway_attributes(item, mapping, *_):
    return {"gateway_id": _selector(item["gateway_selector"], mapping),
            "segment_id": _reference(item["segment_candidate_ref"], mapping),
            "gateway_kind": item["gateway_kind"]}


def _security_attributes(item, mapping, resource_id, observations):
    plane = _fact_plane(item, observations)
    return {"device_id": _reference(item["device_candidate_ref"], mapping),
            "interface_id": _reference(item["interface_candidate_ref"], mapping),
            "zone_id": _reference(item["zone_context_ref"], mapping),
            "configured_state": "configured" if plane == "configured" else "unknown",
            "operational_state": "observed" if plane == "operational" else "unknown"}


def _wan_attributes(item, mapping, *_):
    return {"connection_type": item["connection_type"],
            "local_id": _selector(item["local_selector"], mapping),
            "remote_id": _selector(item["remote_selector"], mapping),
            "state": item["state"] or "unknown"}


def _reference(value, mapping):
    if value not in mapping:
        raise CandidateConversionError("candidate reference is outside the current chunk")
    return mapping[value]


def _optional_reference(value, mapping):
    return None if value is None else _reference(value, mapping)


def _selector(value, mapping):
    candidate = value.get("candidate_ref")
    if candidate is not None:
        return _reference(candidate, mapping)
    digest = hashlib.sha256(repr(sorted(value.items())).encode()).hexdigest()[:20]
    return f"unresolved-selector:{digest}"


def _parsing_state(family, item):
    if family == "adjacencies":
        explicit = item["kind"] in {"lldp", "cdp"} and bool(item["attachments"])
        return "verified" if item["layer"] == "physical" and explicit else "unresolved"
    if family == "routing_relationships" and str(item["session_state"]).lower() not in {
        "up", "established", "full"
    }:
        return "unresolved"
    if family == "tunnels":
        return "unresolved"
    if family == "wan_connections" and item["connection_type"] == "circuit":
        return "unresolved"
    return "verified"


def _fact_plane(item, observations):
    ids = {ref["observation_id"] for ref in item["evidence_refs"]}
    planes = {obs.get("fact_plane") for obs in observations if obs.get("observation_id") in ids}
    return next(iter(planes)) if len(planes) == 1 else "mixed"


def _require_semantic_support(family, item, resolved):
    if family == "segments":
        try:
            network = ip_network(item["canonical_cidr"], strict=False)
        except ValueError as exc:
            raise CandidateConversionError("segment network is invalid") from exc
        if any(_supports_network(value, network) for value in resolved):
            return
    text = " ".join(_as_text(value) for value in resolved).lower()
    tokens = _support_tokens(family, item)
    if tokens and not any(token.lower() in text for token in tokens):
        raise CandidateConversionError("evidence content does not support candidate fact")


def _supports_network(value, expected):
    """Compare a segment with addresses in the already-bound evidence only."""
    if isinstance(value, str) and "/" in value:
        try:
            return ip_network(value, strict=False) == expected
        except ValueError:
            return False
    if isinstance(value, dict):
        # Structured address records carry the host and prefix separately.
        address, prefix = value.get("start"), value.get("bits")
        if isinstance(address, str) and type(prefix) is int:
            try:
                if ip_network(f"{address}/{prefix}", strict=False) == expected:
                    return True
            except ValueError:
                pass
        return any(_supports_network(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_supports_network(child, expected) for child in value)
    return False


def _support_tokens(family, item):
    if family == "devices":
        return [alias["value"] for alias in item["identity_aliases"]]
    keys = {
        "interfaces": ("raw_name",), "segments": ("canonical_cidr",),
        "routes": ("prefix",), "routing_relationships": ("protocol",),
        "tunnels": ("tunnel_id", "remote_endpoint"),
        "wan_connections": ("connection_type",), "adjacencies": ("kind",),
    }.get(family, ())
    return [str(item[key]) for key in keys if item.get(key)]


def _as_text(value):
    if isinstance(value, str):
        return value
    return repr(value)


def _convert_unresolved(items, mapping, resource_id, observations):
    output = []
    catalog = []
    for item in items:
        try:
            provenance, _ = bind_observed_evidence(item["evidence_refs"], observations, resource_id)
        except EvidenceValidationError as exc:
            raise CandidateConversionError("unresolved observation evidence invalid") from exc
        output.append({"id": _canonical_unresolved(item, resource_id),
                       "selector": _formal_selector(item["selector"], mapping),
                       "reason_code": item["reason_code"],
                       "candidate_matches": sorted(mapping.get(value, value)
                                                   for value in item["candidate_matches"]),
                       "provenance_ids": [value["id"] for value in provenance]})
        catalog.extend(provenance)
    return output, catalog


def _formal_selector(selector, mapping):
    value = deepcopy(selector)
    if "candidate_ref" in value:
        value["candidate_ref"] = _reference(value["candidate_ref"], mapping)
    return value


def _canonical_unresolved(item, resource_id):
    digest = hashlib.sha256(item["candidate_id"].encode()).hexdigest()[:20]
    return f"unresolved:{resource_id}:{digest}"


def _unique_provenance(items):
    by_id = {item["id"]: item for item in items}
    return [by_id[key] for key in sorted(by_id)]
