"""Shared request, serialization, and data-access helpers for topology views."""

from __future__ import annotations

from sanic import json as sanic_json
from sanic.request import Request
import orjson

import settings
from models.network_topology import NetworkTopology
from services.network_topology import compatibility_manager as compatibility


def _err(message: str, status: int = 400):
    return sanic_json(
        {"code": 4000, "message": message}, status=status, dumps=orjson.dumps
    )


def _request_data(request: Request) -> dict:
    data = request.json or {}
    if not isinstance(data, dict):
        raise ValueError("请求参数必须是对象")
    return data


def _tenant_id(request: Request) -> str:
    tenant_id = request.ctx.session.get("tenant_id")
    if not tenant_id:
        raise ValueError("租户不存在")
    return tenant_id


def _actor_id(request: Request) -> str:
    session = request.ctx.session
    account = session.get("account_info")
    return str(session.get("sub") or getattr(account, "id", "")).strip()


def _compatibility_error(error: compatibility.CompatibilityError):
    responses = {
        compatibility.TOPOLOGY_CREATE_INVALID: ("拓扑创建参数不合法", 400),
        compatibility.TOPOLOGY_NAME_CONFLICT: ("拓扑名称已存在", 400),
        compatibility.TOPOLOGY_LEGACY_WRITE_REJECTED: (
            "v2 拓扑不允许通过旧接口修改",
            409,
        ),
    }
    message, status = responses.get(error.code, ("拓扑兼容操作失败", 500))
    return _err(message, status=status)


def _legacy_write_error(topology):
    try:
        compatibility.require_legacy_write_allowed(topology)
    except compatibility.CompatibilityError as exc:
        return _compatibility_error(exc)
    return None


def _capabilities(topology) -> dict:
    compatibility_flags = compatibility.compatibility_capabilities(topology)
    return {
        **compatibility_flags,
        "ingress": settings.TOPOLOGY_V2_INGRESS_ENABLED,
        "analysis": settings.TOPOLOGY_V2_ANALYSIS_ENABLED,
        "publish": settings.TOPOLOGY_V2_PUBLISH_ENABLED,
        "view": settings.TOPOLOGY_V2_VIEW_ENABLED,
        "presentation_write": settings.TOPOLOGY_V2_PRESENTATION_WRITE_ENABLED,
    }


def _topology_payload(topology) -> dict:
    payload = topology.json
    return {**payload, "capabilities": _capabilities(topology)}


def _version_generation_source(schema: str | None) -> str | None:
    if schema == compatibility.V2_SCHEMA_VERSION:
        return compatibility.PLAYBOOK_GENERATION_SOURCE
    if schema in (None, compatibility.LEGACY_SCHEMA_VERSION):
        return compatibility.MANUAL_GENERATION_SOURCE
    return None


def _v2_view_disabled(topology) -> bool:
    schema = getattr(topology, "document_schema_version", None)
    return not settings.TOPOLOGY_V2_VIEW_ENABLED and schema not in (
        None,
        compatibility.LEGACY_SCHEMA_VERSION,
    )


async def _tenant_topology(
    topology_id: str,
    tenant_id: str,
    *,
    connection=None,
    lock: bool = False,
):
    query = NetworkTopology.filter(id=topology_id, deleted=False, tenant_id=tenant_id)
    if connection is not None:
        query = query.using_db(connection)
    if lock:
        query = query.select_for_update()
    return await query.first()


async def _name_exists(
    name: str, tenant_id: str, *, exclude_id: str | None = None, connection=None
) -> bool:
    query = NetworkTopology.filter(name=name, deleted=False, tenant_id=tenant_id)
    if exclude_id:
        query = query.exclude(id=exclude_id)
    if connection is not None:
        query = query.using_db(connection)
    return await query.exists()


def _topology_state(topology: NetworkTopology) -> dict:
    return {
        "name": topology.name,
        "topology_json": topology.topology_json or {"nodes": [], "edges": []},
        "layout_json": topology.layout_json or {},
        "source_hash": topology.source_hash,
    }


def _graph_items_by_id(graph: dict, key: str, *, ignore_position: bool = False) -> dict:
    result = {}
    for item in graph.get(key, []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        value = dict(item)
        if ignore_position:
            value.pop("position", None)
        result[item_id] = value
    return result


def _build_change_summary(
    before: dict | None,
    after: dict,
    *,
    change_type: str,
) -> str:
    before_graph = (before or {}).get("topology_json") or {"nodes": [], "edges": []}
    after_graph = after.get("topology_json") or {"nodes": [], "edges": []}
    parts = []

    if before is not None and before.get("name") != after.get("name"):
        parts.append("名称变更")

    labels = (("nodes", "节点", True), ("edges", "连线", False))
    for key, label, ignore_position in labels:
        before_items = _graph_items_by_id(
            before_graph, key, ignore_position=ignore_position
        )
        after_items = _graph_items_by_id(
            after_graph, key, ignore_position=ignore_position
        )
        before_ids = set(before_items)
        after_ids = set(after_items)
        added = len(after_ids - before_ids)
        removed = len(before_ids - after_ids)
        modified = sum(
            before_items[item_id] != after_items[item_id]
            for item_id in before_ids & after_ids
        )
        if added:
            parts.append(f"新增{label} {added} 个")
        if removed:
            parts.append(f"删除{label} {removed} 个")
        if modified:
            parts.append(f"修改{label} {modified} 个")

    if before is not None:
        before_layout = before.get("layout_json") or {}
        after_layout = after.get("layout_json") or {}
        common_node_ids = set(_graph_items_by_id(before_graph, "nodes")) & set(
            _graph_items_by_id(after_graph, "nodes")
        )
        adjusted = sum(
            before_layout.get(node_id) != after_layout.get(node_id)
            for node_id in common_node_ids
        )
        if adjusted:
            parts.append(f"调整 {adjusted} 个节点布局")
        if before.get("source_hash") != after.get("source_hash"):
            parts.append("设备配置已同步")

    if parts:
        return "；".join(parts)
    return {
        "create": "创建拓扑",
        "manual_save": "手动保存拓扑",
        "update": "更新拓扑",
        "config_sync": "同步设备配置",
        "layout": "保存拓扑布局",
    }.get(change_type, "拓扑已更新")
