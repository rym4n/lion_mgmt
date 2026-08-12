"""Mappings for routing observations."""

from __future__ import annotations

from typing import Any

from ..base import MappingContext, _fact, _slug, map_fhrp, map_routing_neighbors


class RoutingMixin:
    def _routes(self, ctx: MappingContext) -> dict[str, list[dict]]:
        contexts, routes, unresolved = [], [], []
        for item in ctx.data["routes"]:
            provenance_id, _ = ctx.provenance("route", "/routes/0/prefix")
            if not self._has_known_vrf(ctx, item["vrf"]):
                contexts.append(self._implicit_vrf(ctx, item["vrf"], provenance_id))
            interface_id = ctx.interface_id(item["interface"])
            next_hop_id = (
                f"unresolved:next-hop:{item['next_hop']}" if item["next_hop"] else None
            )
            attributes = {
                "route_type": item["route_type"],
                "prefix": item["prefix"],
                "vrf_id": ctx.vrf_id(item["vrf"]),
                "next_hop_id": next_hop_id,
                "next_hop_address": item["next_hop"],
                "interface_id": interface_id
                or f"unresolved:interface:{ctx.resource_id}:{_slug(item['interface'])}",
                "metric": None,
                "preference": None,
                "state": item["state"],
            }
            route_key = (
                item["route_type"]
                if item["route_type"] == "default"
                else _slug(item["prefix"])
            )
            routes.append(
                _fact(
                    f"route:{ctx.resource_id}:{route_key}",
                    "route",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                )
            )
            unresolved.extend(
                self._route_unresolved(ctx, item, provenance_id, interface_id)
            )
        result = {
            "network_contexts": contexts,
            "routes": routes,
            "unresolved_references": unresolved,
        }
        if routes:
            _, provenance = ctx.provenance("route", "/routes/0/prefix")
            result["provenance_catalog"] = [provenance]
        return result

    @staticmethod
    def _has_known_vrf(ctx: MappingContext, value: str) -> bool:
        if StructuredParser._has_explicit_vrf(ctx, value):
            return True
        return any(
            item["vrf"] == value
            for item in ctx.items("l3.connected_networks", "networks")
        )

    @staticmethod
    def _route_unresolved(
        ctx: MappingContext,
        item: dict[str, Any],
        provenance_id: str,
        interface_id: str | None,
    ) -> list[dict[str, Any]]:
        values = []
        if item["next_hop"]:
            values.append(
                {
                    "id": f"unresolved:next-hop:{item['next_hop']}",
                    "selector": {"address": item["next_hop"]},
                    "reason_code": "NEXT_HOP_NOT_COLLECTED",
                    "candidate_matches": [],
                    "provenance_ids": [provenance_id],
                }
            )
        if not interface_id:
            values.append(
                {
                    "id": f"unresolved:interface:{ctx.resource_id}:{_slug(item['interface'])}",
                    "selector": {"interface": item["interface"]},
                    "reason_code": "INTERFACE_NOT_COLLECTED",
                    "candidate_matches": [],
                    "provenance_ids": [provenance_id],
                }
            )
        return values
