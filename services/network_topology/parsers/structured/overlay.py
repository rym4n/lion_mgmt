"""Mappings for overlay tunnel observations."""

from __future__ import annotations

from ..base import MappingContext, _fact, _slug, map_sdwan, map_vxlan


class OverlayMixin:
    def _tunnels(self, ctx: MappingContext) -> dict[str, list[dict]]:
        facts, unresolved = [], []
        for item in ctx.data["tunnels"]:
            provenance_id, _ = ctx.provenance("tunnel", "/tunnels/0/tunnel_id")
            interface_id = ctx.interface_id(item["interface"])
            unresolved_interface = (
                f"unresolved:interface:{ctx.resource_id}:{_slug(item['interface'])}"
            )
            attributes = {
                "tunnel_type": item["tunnel_type"],
                "tunnel_id": item["tunnel_id"],
                "context_id": ctx.vrf_id(item["context"]),
                "interface_id": interface_id or unresolved_interface,
                "local_endpoint": item["local_endpoint"],
                "remote_endpoint": item["remote_endpoint"],
                "protected_prefixes": list(item["protected_prefixes"]),
                "state": item["state"],
            }
            facts.append(
                _fact(
                    f"tunnel:{ctx.resource_id}:{_slug(item['tunnel_id'])}",
                    "tunnel",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                )
            )
            unresolved.extend(
                self._tunnel_unresolved(ctx, item, provenance_id, interface_id)
            )
        result = {"tunnels": facts, "unresolved_references": unresolved}
        if facts:
            _, provenance = ctx.provenance("tunnel", "/tunnels/0/tunnel_id")
            result["provenance_catalog"] = [provenance]
        return result

    @staticmethod
    def _tunnel_unresolved(ctx, item, provenance_id, interface_id):
        values = []
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
        values.append(
            {
                "id": f"unresolved:tunnel-peer:{item['remote_endpoint']}",
                "selector": {"address": item["remote_endpoint"]},
                "reason_code": "TUNNEL_PEER_NOT_COLLECTED",
                "candidate_matches": [],
                "provenance_ids": [provenance_id],
            }
        )
        return values
