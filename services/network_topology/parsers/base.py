"""Parser abstraction and the internal deterministic candidate protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Any

from services.network_topology.canonicalization import canonicalize_json, sha256_uri


INTERNAL_CANDIDATE_PROTOCOL = "aioe.network-topology.internal-candidates/v1"


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")


def _cidr_key(value: str) -> str:
    return value.lower().replace("/", "-")


def _fact(fact_id, family, resource_id, attributes, provenance_id, *, state="verified"):
    return {
        "id": fact_id,
        "fact_family": family,
        "resource_ids": [resource_id],
        "source_candidate_ids": [],
        "attributes": attributes,
        "provenance_ids": [provenance_id],
        "parsing_state": state,
    }


class ParserError(ValueError):
    """Fail-closed deterministic parsing error for the current analysis run."""


def _freeze_candidates(
    value: Mapping[str, tuple[dict[str, Any], ...] | list[dict[str, Any]]],
) -> Mapping[str, tuple[dict[str, Any], ...]]:
    frozen: dict[str, tuple[dict[str, Any], ...]] = {}
    for family, items in value.items():
        if not isinstance(family, str) or not family:
            raise ParserError("Candidate family must be a non-empty string")
        frozen[family] = tuple(deepcopy(item) for item in items)
    return MappingProxyType(frozen)


@dataclass(frozen=True)
class CandidateBatch:
    """One immutable parser result bound to an exact chunk and input hash.

    ``candidates`` contains facts-shaped candidate items grouped by the frozen
    facts/v1 array name.  It is deliberately not a facts/v1 document: snapshot,
    analysis-run binding and candidate-to-Facts validation remain later stages.
    """

    parser_id: str
    parser_version: int
    chunk_id: str
    input_hash: str
    resource_id: str
    candidates: Mapping[str, tuple[dict[str, Any], ...] | list[dict[str, Any]]]
    protocol_version: str = INTERNAL_CANDIDATE_PROTOCOL

    def __post_init__(self) -> None:
        values = (self.parser_id, self.chunk_id, self.input_hash, self.resource_id)
        if any(not isinstance(value, str) or not value for value in values):
            raise ParserError("Candidate binding fields must be non-empty strings")
        if type(self.parser_version) is not int or self.parser_version < 1:
            raise ParserError("parser_version must be a positive integer")
        if not self.input_hash.startswith("sha256:") or len(self.input_hash) != 71:
            raise ParserError("input_hash must be a sha256 URI")
        object.__setattr__(self, "candidates", _freeze_candidates(self.candidates))


@dataclass(frozen=True)
class ParserInput:
    """Complete server-bound context supplied to one deterministic parser."""

    chunk_id: str
    resource_id: str
    asset_context: Mapping[str, Any]
    observations: tuple[dict[str, Any], ...]
    resource_observations: tuple[dict[str, Any], ...]
    input_hash: str
    authoritative_context: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_observations(
        cls,
        *,
        chunk_id: str,
        resource_id: str,
        asset_context: Mapping[str, Any],
        observations: tuple[dict[str, Any], ...],
        resource_observations: tuple[dict[str, Any], ...],
        authoritative_context: Mapping[str, Any] | None = None,
        input_hash: str | None = None,
    ) -> "ParserInput":
        authority = authoritative_context or {}
        projection = {
            "chunk_id": chunk_id,
            "resource_id": resource_id,
            "asset_context": asset_context,
            "observations": sorted(
                observations, key=lambda item: item.get("observation_id", "")
            ),
            "authoritative_context": authority,
        }
        bound_hash = input_hash or sha256_uri(canonicalize_json(projection))
        return cls(
            chunk_id=chunk_id,
            resource_id=resource_id,
            asset_context=MappingProxyType(deepcopy(dict(asset_context))),
            observations=tuple(deepcopy(item) for item in observations),
            resource_observations=tuple(
                deepcopy(item) for item in resource_observations
            ),
            input_hash=bound_hash,
            authoritative_context=MappingProxyType(deepcopy(dict(authority))),
        )


class MappingContext:
    """Read-only cross-observation lookup context used by field mappings."""

    def __init__(self, parser_input: ParserInput, observation: dict[str, Any]) -> None:
        self.input = parser_input
        self.observation = observation
        self.resource_id = parser_input.resource_id
        self.asset = parser_input.asset_context
        self.data = observation["data"]
        prefix = self.resource_id.split("-", 1)[0]
        self.vendor_key = "adc" if prefix == "adc" else prefix

    def provenance(self, suffix: str, pointer: str) -> tuple[str, dict[str, Any]]:
        provenance_id = f"p-{self.vendor_key}-{suffix}"
        value = {
            "id": provenance_id,
            "provenance": {
                "type": "observed",
                "resource_id": self.resource_id,
                "observation_id": self.observation["observation_id"],
                "redacted_sha256": self.observation["content_sha256"],
                "locator": {"type": "json_pointer", "pointer": pointer},
            },
        }
        return provenance_id, value

    def observations(self, kind: str) -> list[dict[str, Any]]:
        return [
            item for item in self.input.resource_observations if item["kind"] == kind
        ]

    def items(self, kind: str, field: str) -> list[dict[str, Any]]:
        result = []
        for observation in self.observations(kind):
            if observation.get("observation_status") == "success":
                result.extend(observation["data"].get(field, []))
        return result

    def interface_id(self, name: str | None) -> str | None:
        if not name:
            return None
        for item in self.items("interface.inventory", "interfaces"):
            if name in {item["name"], item["raw_name"]}:
                return f"interface:{self.resource_id}:{_slug(item['raw_name'])}"
        return None

    def vrf_id(self, value: str | None) -> str | None:
        if not value:
            return None
        for item in self.items("l3.vrfs", "vrfs"):
            if value in {item["local_id"], item["name"]}:
                return f"vrf:{self.resource_id}:{_slug(item['local_id'])}"
        return f"vrf:{self.resource_id}:{_slug(value)}"

    def zone_id(self, value: str | None) -> str | None:
        if not value:
            return None
        for item in self.items("security.zones", "zones"):
            if value in {item["local_id"], item["name"]}:
                return f"zone:{self.resource_id}:{_slug(item['local_id'])}"
        return f"zone:{self.resource_id}:{_slug(value)}"


def result_with_provenance(ctx, facts, family, suffix, pointer):
    result = {family: facts}
    if facts:
        _, provenance = ctx.provenance(suffix, pointer)
        result["provenance_catalog"] = [provenance]
    return result


def map_stp(ctx):
    facts = []
    for item in ctx.data["instances"]:
        provenance_id, _ = ctx.provenance("stp", "/instances/0/instance_id")
        attributes = {"protocol": "stp", **deepcopy(item)}
        facts.append(
            _fact(
                f"routing:{ctx.resource_id}:stp:{_slug(item['instance_id'])}",
                "routing_relationship",
                ctx.resource_id,
                attributes,
                provenance_id,
            )
        )
    return result_with_provenance(
        ctx, facts, "routing_relationships", "stp", "/instances/0/instance_id"
    )


def map_routing_neighbors(ctx):
    facts = []
    for item in ctx.data["neighbors"]:
        provenance_id, _ = ctx.provenance(
            "routing-neighbor", "/neighbors/0/remote_address"
        )
        state = "verified" if item["session_state"] == "up" else "unresolved"
        facts.append(
            _fact(
                f"routing:{ctx.resource_id}:{_slug(item['protocol'])}:{_slug(item['remote_address'])}",
                "routing_relationship",
                ctx.resource_id,
                deepcopy(item),
                provenance_id,
                state=state,
            )
        )
    return result_with_provenance(
        ctx,
        facts,
        "routing_relationships",
        "routing-neighbor",
        "/neighbors/0/remote_address",
    )


def map_fhrp(ctx):
    facts = []
    for item in ctx.data["groups"]:
        provenance_id, _ = ctx.provenance("fhrp", "/groups/0/vip")
        attributes = {
            "gateway_id": f"device:{ctx.resource_id}",
            "segment_id": f"network:{ctx.resource_id}:{_slug(item['vip'])}",
            "gateway_kind": "fhrp",
            "virtual_address": item["vip"],
        }
        fact_id = f"gateway:{ctx.resource_id}:{_slug(item['protocol'])}-{_slug(item['group_id'])}"
        facts.append(
            _fact(fact_id, "gateway", ctx.resource_id, attributes, provenance_id)
        )
    return result_with_provenance(ctx, facts, "gateways", "fhrp", "/groups/0/vip")


def map_vxlan(ctx):
    facts = []
    for item in ctx.data["segments"]:
        provenance_id, _ = ctx.provenance("vxlan", "/segments/0/vni")
        attributes = {
            "tunnel_type": "vxlan",
            "tunnel_id": str(item["vni"]),
            "context_id": ctx.vrf_id(item["vrf"]),
            "interface_id": None,
            "local_endpoint": item["vtep"],
            "remote_endpoint": item["peers"][0] if item["peers"] else item["vtep"],
            "protected_prefixes": [],
            "state": item["state"],
        }
        facts.append(
            _fact(
                f"tunnel:{ctx.resource_id}:vxlan-{item['vni']}",
                "tunnel",
                ctx.resource_id,
                attributes,
                provenance_id,
            )
        )
    return result_with_provenance(ctx, facts, "tunnels", "vxlan", "/segments/0/vni")


def map_sdwan(ctx):
    facts = []
    for item in ctx.data["peers"]:
        provenance_id, _ = ctx.provenance("sdwan", "/peers/0/remote_system_id")
        attributes = {
            "connection_type": "sdwan",
            "local_id": item["local_system_id"],
            "remote_id": item["remote_system_id"],
            "provider": None,
            "circuit_id": None,
            "transport": item["transport"],
            "state": item["state"],
        }
        fact_id = f"wan:{ctx.resource_id}:{_slug(item['local_system_id'])}:{_slug(item['remote_system_id'])}"
        facts.append(_fact(fact_id, "wan", ctx.resource_id, attributes, provenance_id))
    return result_with_provenance(
        ctx, facts, "wan_connections", "sdwan", "/peers/0/remote_system_id"
    )


def map_subnets(ctx):
    facts = []
    for item in ctx.data["subnets"]:
        provenance_id, _ = ctx.provenance("subnet", "/subnets/0/cidr")
        attributes = {
            "canonical_cidr": item["cidr"],
            "network_domain_id": item["network_domain_id"],
            "device_id": None,
            "context_id": ctx.vrf_id(item["vrf"]),
            "vrf_id": ctx.vrf_id(item["vrf"]),
            "vlan_id": None,
            "vni": None,
            "gateway_ids": [],
            "name": item["name"],
            "scope_ids": list(item["scope_ids"]),
        }
        fact_id = f"network:{_slug(item['network_domain_id'])}:{_slug(item['cidr'])}"
        facts.append(
            _fact(fact_id, "network", ctx.resource_id, attributes, provenance_id)
        )
    return result_with_provenance(ctx, facts, "segments", "subnet", "/subnets/0/cidr")


def map_neighbor_cache(ctx):
    facts = []
    for item in ctx.data["neighbors"]:
        if not item["infrastructure_relevant"]:
            continue
        provenance_id, _ = ctx.provenance("neighbor-cache", "/neighbors/0/ip")
        attributes = {
            "neighbor_type": "l3_neighbor",
            "local_id": ctx.interface_id(item["interface"]),
            "remote_id": f"unresolved:ip:{item['ip']}",
            "attachments": [item["interface"]],
            "layer": "l3",
            "context_id": ctx.vrf_id(item["vrf"]),
            "state": item["state"],
        }
        facts.append(
            _fact(
                f"neighbor:{ctx.resource_id}:{_slug(item['ip'])}",
                "neighbor",
                ctx.resource_id,
                attributes,
                provenance_id,
                state="unresolved",
            )
        )
    return result_with_provenance(
        ctx, facts, "adjacencies", "neighbor-cache", "/neighbors/0/ip"
    )


class DeterministicParser(ABC):
    """Abstract versioned deterministic parser."""

    parser_id: str
    parser_version: int

    @abstractmethod
    def parse(self, parser_input: ParserInput) -> CandidateBatch:
        """Return candidates or raise ParserError without legacy fallback."""
