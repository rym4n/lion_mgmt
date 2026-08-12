"""Mappings for interface inventory observations."""

from __future__ import annotations

from typing import Any

from ..base import MappingContext, _fact, _slug


class InterfaceMixin:
    def _interfaces(self, ctx: MappingContext) -> dict[str, list[dict]]:
        facts = []
        for item in ctx.data["interfaces"]:
            provenance_id, _ = ctx.provenance("interface", "/interfaces/0/name")
            interface_id = f"interface:{ctx.resource_id}:{_slug(item['raw_name'])}"
            vrf_name = self._interface_vrf(ctx, item)
            vrf_id, zone_id = ctx.vrf_id(vrf_name), ctx.zone_id(item.get("zone"))
            attributes = {
                "device_id": f"device:{ctx.resource_id}",
                "context_id": vrf_id,
                "canonical_name": item["name"],
                "raw_name": item["raw_name"],
                "if_index": item.get("if_index"),
                "interface_type": item["interface_type"],
                "mac": item.get("mac"),
                "parent_interface_id": self._interface_ref(ctx, item.get("parent")),
                "lag_id": self._interface_ref(ctx, item.get("lag_id")),
                "addresses": [value["prefix"] for value in item["addresses"]],
                "vrf_id": vrf_id,
                "zone_id": zone_id,
                "vlan_ids": [int(value) for value in item.get("vlan_refs", [])],
                "speed_bps": item.get("speed_bps"),
                "mtu": item.get("mtu"),
                "configured_state": "present",
                "operational_state": item["oper_state"],
            }
            facts.append(
                _fact(
                    interface_id,
                    "interface",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                )
            )
        result = {"interfaces": facts}
        if facts:
            _, provenance = ctx.provenance("interface", "/interfaces/0/name")
            result["provenance_catalog"] = [provenance]
        return result

    @staticmethod
    def _interface_vrf(ctx: MappingContext, item: dict[str, Any]) -> str | None:
        if item.get("vrf"):
            return item["vrf"]
        for route in ctx.items("routing.connected_static_default", "routes"):
            if route["interface"] in {item["name"], item["raw_name"]}:
                return route["vrf"]
        return None

    @staticmethod
    def _interface_ref(ctx: MappingContext, name: str | None) -> str | None:
        return None if not name else f"interface:{ctx.resource_id}:{_slug(name)}"
