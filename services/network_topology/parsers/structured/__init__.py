"""Deterministic field mappings for the frozen structured observations."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from services.network_topology.canonicalization import (
    CanonicalizationError,
    validate_observation_hashes,
)
from services.network_topology.contracts import (
    ContractError,
    load_schema,
    validate_contract,
)

from ..base import (
    CandidateBatch,
    DeterministicParser,
    MappingContext,
    ParserError,
    ParserInput,
    result_with_provenance,
)
from .interface import InterfaceMixin as _InterfaceMixin
from .l2 import L2Mixin as _L2Mixin, map_stp
from .l3 import L3Mixin as _L3Mixin, map_neighbor_cache
from .misc import MiscMixin as _MiscMixin, map_subnets
from .overlay import OverlayMixin as _OverlayMixin, map_sdwan, map_vxlan
from .routing import RoutingMixin as _RoutingMixin, map_fhrp, map_routing_neighbors
from .security import SecurityMixin as _SecurityMixin
from .system import SystemMixin as _SystemMixin


__all__ = [
    "Any",
    "Callable",
    "CandidateBatch",
    "CanonicalizationError",
    "ContractError",
    "DeterministicParser",
    "FACT_FAMILIES",
    "MappingContext",
    "PARSER_ID",
    "PARSER_VERSION",
    "ParserError",
    "ParserInput",
    "StructuredParser",
    "deepcopy",
    "defaultdict",
    "load_schema",
    "map_fhrp",
    "map_neighbor_cache",
    "map_routing_neighbors",
    "map_sdwan",
    "map_stp",
    "map_subnets",
    "map_vxlan",
    "re",
    "result_with_provenance",
    "validate_contract",
    "validate_observation_hashes",
]


PARSER_ID = "aioe.network-topology.parser/structured-json/v1"
PARSER_VERSION = 1
FACT_FAMILIES = (
    "devices",
    "device_groups",
    "network_contexts",
    "interfaces",
    "segments",
    "adjacencies",
    "routes",
    "routing_relationships",
    "tunnels",
    "memberships",
    "gateways",
    "security_boundaries",
    "wan_connections",
    "unresolved_references",
    "provenance_catalog",
)


class StructuredParser(
    _SystemMixin,
    _InterfaceMixin,
    _L2Mixin,
    _L3Mixin,
    _RoutingMixin,
    _SecurityMixin,
    _OverlayMixin,
    _MiscMixin,
    DeterministicParser,
):
    """Parse only frozen structured observations with deterministic mappings."""

    parser_id = PARSER_ID
    parser_version = PARSER_VERSION

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[MappingContext], dict[str, list[dict]]]] = {
            "system.identity": self._identity,
            "system.virtual_contexts": self._virtual_contexts,
            "system.cluster_members": self._cluster_members,
            "interface.inventory": self._interfaces,
            "l2.vlans": self._vlans,
            "l2.link_aggregation": self._aggregations,
            "l2.neighbors": self._neighbors,
            "l2.stp_summary": map_stp,
            "l3.vrfs": self._vrfs,
            "l3.connected_networks": self._networks,
            "routing.connected_static_default": self._routes,
            "routing.dynamic_neighbors": map_routing_neighbors,
            "routing.first_hop_redundancy": map_fhrp,
            "security.zones": self._zones,
            "security.zone_bindings": self._zone_bindings,
            "overlay.tunnels": self._tunnels,
            "overlay.vxlan_evpn": map_vxlan,
            "wan.sdwan": map_sdwan,
            "ipam.subnets": map_subnets,
            "service.virtual_addresses": self._virtual_addresses,
            "l3.neighbor_cache": map_neighbor_cache,
        }

    @property
    def supported_kinds(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def parse(self, parser_input: ParserInput) -> CandidateBatch:
        candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for observation in parser_input.observations:
            self._validate_observation(observation)
            handler = self._handlers.get(observation["kind"])
            if handler is None:
                raise ParserError("Structured parser does not support observation kind")
            mapped = handler(MappingContext(parser_input, observation))
            for family, items in mapped.items():
                if items:
                    candidates[family].extend(items)
        return CandidateBatch(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            chunk_id=parser_input.chunk_id,
            input_hash=parser_input.input_hash,
            resource_id=parser_input.resource_id,
            candidates=dict(candidates),
        )

    @staticmethod
    def _validate_observation(observation: dict[str, Any]) -> None:
        schema_uri = observation.get("data_schema")
        try:
            schema = load_schema(schema_uri)
            encoded = validate_observation_hashes(observation)
            if len(encoded) > schema["x-max-bytes"]:
                raise ParserError("Observation exceeds the frozen parser size limit")
            validate_contract(observation, schema_uri)
        except (CanonicalizationError, ContractError, KeyError, TypeError) as exc:
            raise ParserError("Structured observation validation failed") from exc

    @staticmethod
    def _with_provenance(
        result: dict[str, list[dict]], provenance: dict[str, Any]
    ) -> dict[str, list[dict]]:
        if any(result.values()):
            result["provenance_catalog"] = [provenance]
        return result

    @staticmethod
    def _result_with_first_provenance(
        ctx: MappingContext, facts: list[dict], family: str, suffix: str, pointer: str
    ) -> dict[str, list[dict]]:
        return result_with_provenance(ctx, facts, family, suffix, pointer)


# Preserve the original global lookup used by RoutingMixin._has_known_vrf.
from . import routing as _routing

_routing.StructuredParser = StructuredParser
