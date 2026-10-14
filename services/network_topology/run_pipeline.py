"""Stable collection chunking and deterministic parser orchestration.

This module intentionally stops at internal candidates.  It never imports the
legacy analyzer and does not construct a facts/v1 document.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from services.network_topology.canonicalization import canonicalize_json, sha256_uri

from .parsers.base import CandidateBatch, ParserError, ParserInput, _fact, _slug
from .parsers.registry import ParserRegistry
from .parsers.structured import FACT_FAMILIES, PARSER_ID, PARSER_VERSION


DOMAIN_ORDER = ("identity", "interface", "l2", "l3", "zone", "tunnel", "raw")
KIND_DOMAIN = {
    "system.identity": "identity",
    "system.virtual_contexts": "identity",
    "system.cluster_members": "identity",
    "interface.inventory": "interface",
    "l2.vlans": "l2",
    "l2.link_aggregation": "l2",
    "l2.neighbors": "l2",
    "l2.stp_summary": "l2",
    "l3.vrfs": "l3",
    "l3.connected_networks": "l3",
    "routing.connected_static_default": "l3",
    "routing.dynamic_neighbors": "l3",
    "routing.first_hop_redundancy": "l3",
    "ipam.subnets": "l3",
    "l3.neighbor_cache": "l3",
    "security.zones": "zone",
    "security.zone_bindings": "zone",
    "overlay.tunnels": "tunnel",
    "overlay.vxlan_evpn": "tunnel",
    "wan.sdwan": "tunnel",
    "service.virtual_addresses": "tunnel",
    "raw.action_output": "raw",
}


@dataclass(frozen=True)
class ObservationChunk:
    """A stable, whole-value chunk for one resource and observation domain."""

    chunk_id: str
    resource_id: str
    domain: str
    asset_context: dict[str, Any]
    observations: tuple[dict[str, Any], ...]
    resource_observations: tuple[dict[str, Any], ...]
    authoritative_context: dict[str, Any]
    input_hash: str


def build_collection_chunks(collection: dict[str, Any]) -> tuple[ObservationChunk, ...]:
    """Group whole observations by resource/domain with stable IDs and hashes."""
    if not isinstance(collection, dict):
        raise ParserError("Collection must be an object")
    resources = _collection_resources(collection)
    authority = deepcopy(collection.get("authoritative_context", {}))
    chunks: list[ObservationChunk] = []
    for resource in sorted(resources, key=lambda item: item["resource_id"]):
        chunks.extend(_resource_chunks(resource, authority))
    return tuple(chunks)


def _collection_resources(collection: dict[str, Any]) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in collection.get("sources", []):
        for resource in source.get("resources", []):
            resource_id = resource.get("resource_id")
            if (
                not isinstance(resource_id, str)
                or not resource_id
                or resource_id in seen
            ):
                raise ParserError(
                    "Collection resource IDs must be non-empty and unique"
                )
            seen.add(resource_id)
            resources.append(resource)
    return resources


def _resource_chunks(
    resource: dict[str, Any], authority: dict[str, Any]
) -> list[ObservationChunk]:
    observations = tuple(resource.get("result", {}).get("observations", []))
    successful = [
        deepcopy(item)
        for item in observations
        if item.get("observation_status") == "success"
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in successful:
        domain = KIND_DOMAIN.get(observation.get("kind"))
        if domain is None:
            raise ParserError(
                "Successful observation kind has no registered chunk domain"
            )
        grouped[domain].append(observation)
    return [
        _make_chunk(resource, domain, grouped[domain], observations, authority)
        for domain in DOMAIN_ORDER
        if grouped[domain]
    ]


def _make_chunk(
    resource: dict[str, Any],
    domain: str,
    observations: list[dict[str, Any]],
    resource_observations: tuple[dict[str, Any], ...],
    authority: dict[str, Any],
) -> ObservationChunk:
    resource_id = resource["resource_id"]
    chunk_id = f"{resource_id}:{domain}:v1"
    ordered = tuple(sorted(observations, key=lambda item: item["observation_id"]))
    projection = {
        "chunk_id": chunk_id,
        "resource_id": resource_id,
        "asset_context": resource["asset_context"],
        "observations": ordered,
        "authoritative_context": authority if domain == "identity" else {},
    }
    return ObservationChunk(
        chunk_id=chunk_id,
        resource_id=resource_id,
        domain=domain,
        asset_context=deepcopy(resource["asset_context"]),
        observations=ordered,
        resource_observations=tuple(deepcopy(item) for item in resource_observations),
        authoritative_context=deepcopy(authority),
        input_hash=sha256_uri(canonicalize_json(projection)),
    )


def parse_collection_candidates(
    collection: dict[str, Any], registry: ParserRegistry | None = None
) -> tuple[CandidateBatch, ...]:
    """Parse all successful observations; any error fails closed to the caller."""
    selected_registry = registry or ParserRegistry.default()
    chunks = build_collection_chunks(collection)
    parsed: list[tuple[str, CandidateBatch]] = []
    for chunk in chunks:
        parsed.extend(_parse_chunk(chunk, selected_registry))
    parsed.extend(_parse_authority(chunks))
    return _order_candidate_batches(parsed)


def _parse_chunk(
    chunk: ObservationChunk, registry: ParserRegistry
) -> list[tuple[str, CandidateBatch]]:
    results: list[tuple[str, CandidateBatch]] = []
    source_order = {
        item["observation_id"]: index
        for index, item in enumerate(chunk.resource_observations)
    }
    ordered = sorted(
        chunk.observations, key=lambda item: source_order[item["observation_id"]]
    )
    for observation in ordered:
        parser_input = ParserInput.from_observations(
            chunk_id=chunk.chunk_id,
            resource_id=chunk.resource_id,
            asset_context=chunk.asset_context,
            observations=(observation,),
            resource_observations=chunk.resource_observations,
            authoritative_context=chunk.authoritative_context,
            input_hash=chunk.input_hash,
        )
        parser = registry.resolve_input(parser_input)
        results.append((observation["kind"], parser.parse(parser_input)))
    return results


def _parse_authority(
    chunks: tuple[ObservationChunk, ...],
) -> list[tuple[str, CandidateBatch]]:
    results = []
    identity_chunks = [chunk for chunk in chunks if chunk.domain == "identity"]
    for chunk in identity_chunks:
        if not any(chunk.authoritative_context.values()):
            continue
        parser_input = ParserInput.from_observations(
            chunk_id=chunk.chunk_id,
            resource_id=chunk.resource_id,
            asset_context=chunk.asset_context,
            observations=(),
            resource_observations=chunk.resource_observations,
            authoritative_context=chunk.authoritative_context,
            input_hash=chunk.input_hash,
        )
        results.append(
            ("authoritative_context", authoritative_candidates(parser_input))
        )
    return results


def _order_candidate_batches(
    parsed: list[tuple[str, CandidateBatch]],
) -> tuple[CandidateBatch, ...]:
    output: list[CandidateBatch] = []
    for family in FACT_FAMILIES:
        eligible = [
            (kind, batch) for kind, batch in parsed if batch.candidates.get(family)
        ]
        eligible.sort(key=lambda value: _family_order(family, value[0]))
        for _, batch in eligible:
            output.append(_single_family_batch(batch, family))
    return tuple(output)


def _family_order(family: str, kind: str) -> int:
    if family == "network_contexts":
        return {
            "l3.vrfs": 0,
            "l2.vlans": 1,
            "security.zones": 2,
            "l3.connected_networks": 3,
            "routing.connected_static_default": 4,
        }.get(kind, 5)
    if family == "provenance_catalog":
        return {
            "system.identity": 0,
            "interface.inventory": 1,
            "system.cluster_members": 2,
            "l2.vlans": 3,
            "l2.link_aggregation": 4,
            "l2.neighbors": 5,
            "l3.connected_networks": 6,
            "security.zones": 7,
            "security.zone_bindings": 8,
            "l3.vrfs": 9,
            "service.virtual_addresses": 10,
            "routing.connected_static_default": 11,
            "overlay.tunnels": 12,
            "authoritative_context": 100,
        }.get(kind, 50)
    return 0


def _single_family_batch(batch: CandidateBatch, family: str) -> CandidateBatch:
    return CandidateBatch(
        parser_id=batch.parser_id,
        parser_version=batch.parser_version,
        chunk_id=batch.chunk_id,
        input_hash=batch.input_hash,
        resource_id=batch.resource_id,
        candidates={family: batch.candidates[family]},
    )


def authoritative_candidates(parser_input: ParserInput) -> CandidateBatch:
    """Map collection authority records into the same bound candidate protocol."""
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    context = parser_input.authoritative_context
    resource_id = parser_input.resource_id
    vendor_key = resource_id.split("-", 1)[0]
    for record in context.get("external_networks", []):
        _authority_network(candidates, record, resource_id, vendor_key, "external")
    for record in context.get("circuits", []):
        _authority_circuit(candidates, record, resource_id, vendor_key)
    for record in context.get("cloud_networks", []):
        _authority_network(candidates, record, resource_id, vendor_key, "cloud")
    return CandidateBatch(
        parser_id=PARSER_ID,
        parser_version=PARSER_VERSION,
        chunk_id=parser_input.chunk_id,
        input_hash=parser_input.input_hash,
        resource_id=resource_id,
        candidates=dict(candidates),
    )


def _authority_provenance(record, identifier):
    return {
        "id": identifier,
        "provenance": {"type": "authoritative", **record["provenance"]},
    }


def _authority_network(candidates, record, resource_id, vendor_key, kind):
    provenance_id = f"p-{vendor_key}-authority-{kind}"
    attributes = {
        "canonical_cidr": record["attributes"]["cidr"],
        "network_domain_id": record["attributes"].get(
            "network_domain", "synthetic-cloud"
        ),
        "device_id": None,
        "context_id": None,
        "vrf_id": None,
        "vlan_id": None,
        "vni": None,
        "gateway_ids": [],
        "name": record["name"],
        "scope_ids": list(record["scope_ids"]),
    }
    record_name = record["record_id"].split(":", 1)[1]
    candidates["segments"].append(
        _fact(
            f"network:{record_name}", "network", resource_id, attributes, provenance_id
        )
    )
    candidates["provenance_catalog"].append(
        _authority_provenance(record, provenance_id)
    )


def _authority_circuit(candidates, record, resource_id, vendor_key):
    provenance_id = f"p-{vendor_key}-authority-circuit"
    attributes = {
        "connection_type": "circuit",
        "local_id": f"device:{resource_id}",
        "remote_id": f"network:{record['attributes']['remote_record_id'].split(':', 1)[1]}",
        "provider": record["attributes"]["provider"],
        "circuit_id": record["attributes"]["circuit_id"],
        "transport": "private_circuit",
        "state": "unknown",
    }
    circuit_key = _slug(record["attributes"]["circuit_id"]).removeprefix("syn-")
    candidates["wan_connections"].append(
        _fact(
            f"wan:synthetic-{circuit_key}",
            "wan",
            resource_id,
            attributes,
            provenance_id,
        )
    )
    candidates["provenance_catalog"].append(
        _authority_provenance(record, provenance_id)
    )
