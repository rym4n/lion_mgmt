"""Mappings for system observations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..base import MappingContext, _fact, _slug


class SystemMixin:
    def _identity(self, ctx: MappingContext) -> dict[str, list[dict]]:
        provenance_id, provenance = ctx.provenance("identity", "/hostname")
        attributes = {
            "device_type": ctx.data["device_type"],
            "identity_aliases": deepcopy(ctx.data["aliases"]),
            "roles": [ctx.asset["role"]] if ctx.asset.get("role") else [],
            "inventory_state": "managed",
            "vendor": ctx.data["vendor"],
            "model": ctx.data["model"],
            "os_version": ctx.data["os_version"],
            "management_ips": list(ctx.asset.get("management_ips", [])),
        }
        fact = _fact(
            f"device:{ctx.resource_id}",
            "device",
            ctx.resource_id,
            attributes,
            provenance_id,
        )
        return self._with_provenance({"devices": [fact]}, provenance)

    def _virtual_contexts(self, ctx: MappingContext) -> dict[str, list[dict]]:
        facts = []
        for index, item in enumerate(ctx.data["contexts"]):
            provenance_id, provenance = ctx.provenance(
                "virtual-context", f"/contexts/{index}/local_id"
            )
            attributes = {
                "owner_id": f"device:{ctx.resource_id}",
                "local_id": item["local_id"],
                "name": item["name"],
                "context_type": item["context_type"],
                "parent_context_id": item["parent_context"],
            }
            facts.append(
                _fact(
                    f"context:{ctx.resource_id}:{_slug(item['local_id'])}",
                    "network_context",
                    ctx.resource_id,
                    attributes,
                    provenance_id,
                )
            )
        result = {"network_contexts": facts}
        if facts:
            _, provenance = ctx.provenance("virtual-context", "/contexts/0/local_id")
            result["provenance_catalog"] = [provenance]
        return result

    def _cluster_members(self, ctx: MappingContext) -> dict[str, list[dict]]:
        if not ctx.data["members"]:
            return {}
        provenance_id, provenance = ctx.provenance("cluster", "/group_id")
        member_ids = [f"device:{item['member_id']}" for item in ctx.data["members"]]
        group_slug = ctx.resource_id.replace("-lb-", "-cluster-")
        devices = [
            self._discovered_member(ctx, item, provenance_id)
            for item in ctx.data["members"]
            if item["member_id"] != ctx.resource_id
        ]
        group = _fact(
            f"device-group:{group_slug}",
            "device_group",
            ctx.resource_id,
            {
                "group_type": "cluster",
                "identity": ctx.data["group_id"],
                "member_ids": member_ids,
            },
            provenance_id,
        )
        return self._with_provenance(
            {"devices": devices, "device_groups": [group]}, provenance
        )

    @staticmethod
    def _discovered_member(
        ctx: MappingContext, item: dict[str, Any], provenance_id: str
    ) -> dict[str, Any]:
        attributes = {
            "device_type": ctx.asset["device_type"],
            "identity_aliases": [],
            "roles": [ctx.asset["role"]] if ctx.asset.get("role") else [],
            "inventory_state": "discovered",
            "vendor": ctx.asset["vendor"],
            "model": ctx.asset["model"],
            "os_version": ctx.asset["os_version"],
        }
        return _fact(
            f"device:{item['member_id']}",
            "device",
            ctx.resource_id,
            attributes,
            provenance_id,
        )
