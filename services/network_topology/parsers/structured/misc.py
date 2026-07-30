"""Mappings for IPAM and virtual-service observations."""

from __future__ import annotations

from ..base import MappingContext, _cidr_key, _fact, _slug, map_subnets


class MiscMixin:
    def _virtual_addresses(self, ctx: MappingContext) -> dict[str, list[dict]]:
        facts = []
        for item in ctx.data["virtual_addresses"]:
            provenance_id, _ = ctx.provenance("vip", "/virtual_addresses/0/address")
            identifier = _slug(item["id"]).removeprefix("syn-")
            attributes = {
                "gateway_id": f"device:{ctx.resource_id}",
                "segment_id": f"network:{ctx.resource_id}:{_cidr_key(item['network_prefix'])}",
                "gateway_kind": "interface",
                "virtual_address": item["address"],
            }
            facts.append(
                _fact(
                    f"gateway:{ctx.resource_id}:{identifier}",
                    "gateway",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                )
            )
        return self._result_with_first_provenance(
            ctx, facts, "gateways", "vip", "/virtual_addresses/0/address"
        )
