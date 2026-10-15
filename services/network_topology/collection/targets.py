"""Freeze tenant-scoped action and resource identities before collection."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from models import Tool, ToolAction, ToolResources
from services.network_topology.collection.errors import CollectionError


@dataclass(frozen=True)
class Target:
    item_id: str
    app_name: str
    app_version: str
    main: str
    action_func: str
    action_name: str
    action_input: dict
    resource_id: str
    resource_name: str
    host: str | None
    asset_instance_id: str | None


class TargetRepository(Protocol):
    async def resource(self, tenant_id, app_name, resource_id) -> dict | None: ...
    async def latest_tool(self, tenant_id, app_name) -> tuple | None: ...
    async def tool(self, tenant_id, app_name, version) -> tuple | None: ...
    async def actions(self, tenant_id, app_name, version) -> list[dict]: ...


class OrmTargetRepository:
    @staticmethod
    def _resource(row):
        return {"id": str(row.id), "name": row.name, "resources_json": row.resources_json}

    async def resource(self, tenant_id, app_name, resource_id):
        row = await ToolResources.filter(id=resource_id, tenant_id=tenant_id, tool_name=app_name,
                                         deleted=False, internal=False).first()
        return self._resource(row) if row else None

    async def latest_tool(self, tenant_id, app_name):
        row = await Tool.filter(tenant_id=tenant_id, name=app_name, maintain=False, deleted=False).order_by("-ctime").first()
        return (row.version, row.main) if row else None

    async def tool(self, tenant_id, app_name, version):
        row = await Tool.filter(tenant_id=tenant_id, name=app_name, version=version, maintain=False, deleted=False).first()
        return (row.version, row.main) if row else None

    async def actions(self, tenant_id, app_name, version):
        rows = await ToolAction.filter(tenant_id=tenant_id, tool_name=app_name, version_number=version, deleted=False)
        return [row.action for row in rows if isinstance(row.action, dict)]


async def freeze_targets(plan, items, *, repository=None) -> list[Target]:
    repo = repository or OrmTargetRepository()
    targets = []
    for item in items:
        tool = await (repo.tool(plan.tenant_id, item.app_name, item.app_version) if item.app_version
                      else repo.latest_tool(plan.tenant_id, item.app_name))
        if tool is None:
            raise CollectionError("COLLECTION_ACTION_NOT_FOUND")
        version, main = tool
        action = next((a for a in await repo.actions(plan.tenant_id, item.app_name, version)
                       if a.get("func") == item.action_func), None)
        if action is None:
            raise CollectionError("COLLECTION_ACTION_NOT_FOUND")
        selected = {}
        for reference in item.resources:
            rid = str(reference["resource_id"])
            resource = await repo.resource(plan.tenant_id, item.app_name, rid)
            if resource is None:
                raise CollectionError("RESOURCE_NOT_FOUND", 409, str(reference.get("resource_name") or rid))
            selected[rid] = resource
        for rid, resource in selected.items():
            config = resource.get("resources_json") or {}
            host = next((config[key].get("value") for key in ("host", "ip")
                         if isinstance(config.get(key), dict) and config[key].get("value")), None)
            targets.append(Target(str(item.id), item.app_name, version, main, item.action_func,
                                  action.get("name") or item.action_func, deepcopy(item.action_input or {}),
                                  rid, resource["name"], host, None))
    return targets
