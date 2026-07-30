"""Mappings for layer-two observations."""

from __future__ import annotations

from typing import Any

from ..base import MappingContext, _fact, _slug, map_stp


class L2Mixin:
    def _vlans(self, ctx: MappingContext) -> dict[str, list[dict]]:
        facts = []
        for item in ctx.data["vlans"]:
            provenance_id, _ = ctx.provenance("vlan", "/vlans/0/vlan_id")
            interfaces = ctx.items("interface.inventory", "interfaces")
            interface_ids = [
                f"interface:{ctx.resource_id}:{_slug(value['raw_name'])}"
                for value in interfaces
                if str(item["vlan_id"]) in value.get("vlan_refs", [])
            ]
            attributes = {
                "owner_id": f"device:{ctx.resource_id}",
                "local_id": str(item["vlan_id"]),
                "name": item["name"],
                "vlan_id": item["vlan_id"],
                "bridge_domain": item.get("bridge_domain"),
                "vni": item.get("vni"),
                "interface_ids": interface_ids,
            }
            facts.append(
                _fact(
                    f"vlan:{ctx.resource_id}:{item['vlan_id']}",
                    "vlan",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                )
            )
        result = {"network_contexts": facts}
        if facts:
            _, provenance = ctx.provenance("vlan", "/vlans/0/vlan_id")
            result["provenance_catalog"] = [provenance]
        return result

    def _aggregations(self, ctx: MappingContext) -> dict[str, list[dict]]:
        interfaces, memberships = [], []
        for item in ctx.data["aggregations"]:
            provenance_id, _ = ctx.provenance("lag", "/aggregations/0/lag_id")
            lag_id = f"interface:{ctx.resource_id}:{_slug(item['interface'])}"
            attributes = {
                "device_id": f"device:{ctx.resource_id}",
                "context_id": None,
                "canonical_name": item["interface"],
                "raw_name": item["interface"],
                "if_index": None,
                "interface_type": "link_aggregation",
                "mac": None,
                "parent_interface_id": None,
                "lag_id": None,
                "addresses": [],
                "vrf_id": None,
                "zone_id": None,
                "vlan_ids": [],
                "speed_bps": None,
                "mtu": None,
                "configured_state": "present",
                "operational_state": item["state"],
            }
            interfaces.append(
                _fact(lag_id, "interface", ctx.resource_id, attributes, provenance_id)
            )
            memberships.extend(self._lag_memberships(ctx, item, lag_id, provenance_id))
        result = {"interfaces": interfaces, "memberships": memberships}
        if interfaces:
            _, provenance = ctx.provenance("lag", "/aggregations/0/lag_id")
            result["provenance_catalog"] = [provenance]
        return result

    @staticmethod
    def _lag_memberships(
        ctx: MappingContext, item: dict[str, Any], lag_id: str, provenance_id: str
    ) -> list[dict[str, Any]]:
        result = []
        for member in item["members"]:
            member_id = (
                ctx.interface_id(member)
                or f"interface:{ctx.resource_id}:{_slug(member)}"
            )
            attributes = {
                "member_id": member_id,
                "container_id": lag_id,
                "membership_kind": "link_aggregation",
            }
            member_key = member_id.rsplit(":", 1)[-1]
            fact_id = (
                f"membership:{ctx.resource_id}:{member_key}:lag{_slug(item['lag_id'])}"
            )
            result.append(
                _fact(fact_id, "membership", ctx.resource_id, attributes, provenance_id)
            )
        return result

    def _neighbors(self, ctx: MappingContext) -> dict[str, list[dict]]:
        facts, unresolved = [], []
        for item in ctx.data["neighbors"]:
            provenance_id, _ = ctx.provenance(
                "neighbor", "/neighbors/0/remote_chassis_id"
            )
            suffix = self._remote_suffix(item["remote_chassis_id"])
            unresolved_id = self._remote_unresolved_id(ctx, item)
            local_id = ctx.interface_id(item["local_interface"])
            attributes = {
                "neighbor_type": item["protocol"],
                "local_id": local_id,
                "remote_id": unresolved_id,
                "attachments": [item["local_interface"], item["remote_port_id"]],
                "layer": "l2",
                "context_id": self._interface_context(ctx, item["local_interface"]),
                "state": item["state"],
            }
            local_key = (
                local_id.rsplit(":", 1)[-1]
                if local_id
                else _slug(item["local_interface"])
            )
            fact_id = f"neighbor:{ctx.resource_id}:{local_key}:remote-{suffix}"
            facts.append(
                _fact(
                    fact_id,
                    "neighbor",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                    state="unresolved",
                )
            )
            unresolved.append(
                {
                    "id": unresolved_id,
                    "selector": {"chassis_id": item["remote_chassis_id"]},
                    "reason_code": "REMOTE_DEVICE_NOT_COLLECTED",
                    "candidate_matches": [],
                    "provenance_ids": [provenance_id],
                }
            )
        result = {"adjacencies": facts, "unresolved_references": unresolved}
        if facts:
            _, provenance = ctx.provenance("neighbor", "/neighbors/0/remote_chassis_id")
            result["provenance_catalog"] = [provenance]
        return result

    @staticmethod
    def _remote_suffix(chassis_id: str) -> str:
        parts = chassis_id.split(":")
        return parts[-2].lower() if len(parts) > 1 else _slug(chassis_id)

    @staticmethod
    def _remote_unresolved_id(ctx: MappingContext, item: dict[str, Any]) -> str:
        if ctx.vendor_key == "h3c":
            return "unresolved:h3c-remote"
        return f"unresolved:chassis:{_slug(item['remote_chassis_id'])}"

    @staticmethod
    def _interface_context(ctx: MappingContext, name: str) -> str | None:
        for item in ctx.items("interface.inventory", "interfaces"):
            if name in {item["name"], item["raw_name"]}:
                return ctx.vrf_id(item.get("vrf"))
        return None
