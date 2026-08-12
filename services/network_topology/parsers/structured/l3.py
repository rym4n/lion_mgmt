"""Mappings for layer-three context and connected-network observations."""

from __future__ import annotations

import re
from typing import Any

from ..base import MappingContext, _cidr_key, _fact, _slug, map_neighbor_cache


class L3Mixin:
    def _vrfs(self, ctx: MappingContext) -> dict[str, list[dict]]:
        facts = []
        for item in ctx.data["vrfs"]:
            provenance_id, _ = ctx.provenance("vrf", "/vrfs/0/name")
            attributes = {
                "owner_id": f"device:{ctx.resource_id}",
                "local_id": item["local_id"],
                "name": item["name"],
                "rd": item["route_distinguishers"][0]
                if item["route_distinguishers"]
                else None,
                "import_route_targets": list(item["route_targets"]),
                "export_route_targets": list(item["route_targets"]),
                "interface_ids": [
                    ctx.interface_id(value)
                    for value in item["interfaces"]
                    if ctx.interface_id(value)
                ],
            }
            facts.append(
                _fact(
                    f"vrf:{ctx.resource_id}:{_slug(item['local_id'])}",
                    "vrf",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                )
            )
        return self._result_with_first_provenance(
            ctx, facts, "network_contexts", "vrf", "/vrfs/0/name"
        )

    def _networks(self, ctx: MappingContext) -> dict[str, list[dict]]:
        contexts, segments = [], []
        for item in ctx.data["networks"]:
            provenance_id, _ = ctx.provenance("network", "/networks/0/prefix")
            vrf_id = ctx.vrf_id(item["vrf"])
            if not self._has_explicit_vrf(ctx, item["vrf"]):
                contexts.append(self._implicit_vrf(ctx, item["vrf"], provenance_id))
            attributes = {
                "canonical_cidr": item["prefix"],
                "network_domain_id": item["network_domain"],
                "device_id": f"device:{ctx.resource_id}",
                "context_id": vrf_id,
                "vrf_id": vrf_id,
                "vlan_id": self._vlan_from_interface(item["interface"]),
                "vni": None,
                "gateway_ids": self._gateway_ids(ctx, item["prefix"]),
            }
            segments.append(
                _fact(
                    f"network:{ctx.resource_id}:{_cidr_key(item['prefix'])}",
                    "network",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                )
            )
        result = {"network_contexts": contexts, "segments": segments}
        if segments:
            _, provenance = ctx.provenance("network", "/networks/0/prefix")
            result["provenance_catalog"] = [provenance]
        return result

    @staticmethod
    def _has_explicit_vrf(ctx: MappingContext, value: str) -> bool:
        return any(
            value in {item["local_id"], item["name"]}
            for item in ctx.items("l3.vrfs", "vrfs")
        )

    @staticmethod
    def _implicit_vrf(
        ctx: MappingContext, value: str, provenance_id: str
    ) -> dict[str, Any]:
        interfaces = []
        for item in ctx.items("interface.inventory", "interfaces"):
            if item.get("vrf") in {value, None} and item["addresses"]:
                interfaces.append(
                    f"interface:{ctx.resource_id}:{_slug(item['raw_name'])}"
                )
        attributes = {
            "owner_id": f"device:{ctx.resource_id}",
            "local_id": _slug(value),
            "name": value,
            "rd": None,
            "import_route_targets": [],
            "export_route_targets": [],
            "interface_ids": interfaces,
        }
        return _fact(
            f"vrf:{ctx.resource_id}:{_slug(value)}",
            "vrf",
            ctx.resource_id,
            attributes,
            provenance_id,
        )

    @staticmethod
    def _vlan_from_interface(name: str) -> int | None:
        match = re.search(r"(?:vlan-interface|vlan)(\d+)", name, re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _gateway_ids(ctx: MappingContext, prefix: str) -> list[str]:
        result = []
        for item in ctx.items("service.virtual_addresses", "virtual_addresses"):
            if item["network_prefix"] == prefix:
                identifier = _slug(item["id"]).removeprefix("syn-")
                result.append(f"gateway:{ctx.resource_id}:{identifier}")
        return result
