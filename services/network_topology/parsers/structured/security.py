"""Mappings for security-zone observations."""

from __future__ import annotations

from ..base import MappingContext, _fact, _slug


class SecurityMixin:
    def _zones(self, ctx: MappingContext) -> dict[str, list[dict]]:
        facts = []
        for item in ctx.data["zones"]:
            provenance_id, _ = ctx.provenance("zone", "/zones/0/name")
            interfaces = [
                ctx.interface_id(binding["interface"])
                for binding in ctx.items("security.zone_bindings", "bindings")
                if binding["zone_id"] == item["local_id"]
            ]
            attributes = {
                "owner_id": f"device:{ctx.resource_id}",
                "local_id": item["local_id"],
                "name": item["name"],
                "interface_ids": [value for value in interfaces if value],
            }
            facts.append(
                _fact(
                    f"zone:{ctx.resource_id}:{_slug(item['local_id'])}",
                    "zone",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                )
            )
        return self._result_with_first_provenance(
            ctx, facts, "network_contexts", "zone", "/zones/0/name"
        )

    def _zone_bindings(self, ctx: MappingContext) -> dict[str, list[dict]]:
        facts = []
        for item in ctx.data["bindings"]:
            provenance_id, _ = ctx.provenance("zone-binding", "/bindings/0/interface")
            interface_id = ctx.interface_id(item["interface"])
            attributes = {
                "device_id": f"device:{ctx.resource_id}",
                "interface_id": interface_id,
                "zone_id": ctx.zone_id(item["zone_id"]),
                "configured_state": "present",
                "operational_state": self._oper_state(ctx, item["interface"]),
            }
            facts.append(
                _fact(
                    f"security:{ctx.resource_id}:{_slug(item['zone_id'])}:{_slug(item['interface'])}",
                    "security",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                )
            )
        return self._result_with_first_provenance(
            ctx, facts, "security_boundaries", "zone-binding", "/bindings/0/interface"
        )

    @staticmethod
    def _oper_state(ctx: MappingContext, name: str) -> str:
        for item in ctx.items("interface.inventory", "interfaces"):
            if name in {item["name"], item["raw_name"]}:
                return item["oper_state"]
        return "unknown"
