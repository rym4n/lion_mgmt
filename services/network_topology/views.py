# coding=utf-8

from datetime import datetime
import hashlib
import math

import orjson
from sanic import Blueprint, json as sanic_json
from sanic.request import Request
from sanic_ext import openapi
from tortoise.expressions import RawSQL
from tortoise.transactions import in_transaction

from core.response import response_ok
from libs.security.authorization import check_permissions, requires_permission
from libs.utils import strip_timezone_to_seconds
from models.network_topology import NetworkTopology
from services.network_topology.analyzer import analyze_topology
from settings import TZ

topology_bp = Blueprint("network_topology", url_prefix="/network-topology")
__all__ = ["topology_bp"]

_MAX_LAYOUT_NODES = 2000
_MAX_GRAPH_EDGES = 10000
_MAX_LAYOUT_COORDINATE = 10_000_000
_MAX_GRAPH_BYTES = 8 * 1024 * 1024
_MAX_GRAPH_DEPTH = 8
_MAX_COLLECTION_ITEMS = 10000
_MAX_OBJECT_FIELDS = 200
_MAX_STRING_LENGTH = 2 * 1024 * 1024
_MAX_GRAPH_ID_LENGTH = 200
_MAX_SOURCE_CONFIG_BYTES = 8 * 1024 * 1024


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


def _parse_pagination(data: dict) -> tuple[int, int]:
    try:
        page = max(int(data.get("page", 1)), 1)
        page_size = min(max(int(data.get("page_size", 15)), 1), 100)
    except (TypeError, ValueError) as exc:
        raise ValueError("分页参数不合法") from exc
    return page, page_size


def _parse_topology_name(data: dict, *, required: bool = True) -> str | None:
    if "name" not in data and not required:
        return None
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("拓扑名称不能为空")
    if len(name) > 200:
        raise ValueError("拓扑名称不能超过 200 个字符")
    return name


def _parse_topology_id(data: dict) -> str:
    raw_id = data.get("id") or data.get("topology_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError("拓扑ID不能为空")
    return raw_id.strip()


def _parse_revision(value, *, required: bool = True) -> int | None:
    if value is None and not required:
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        revision = int(value)
        if revision < 1:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("拓扑版本不合法") from exc
    return revision


def _normalize_bounded_json(value, *, path: str, depth: int = 0):
    if depth > _MAX_GRAPH_DEPTH:
        raise ValueError(f"{path} 嵌套层级过深")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > _MAX_STRING_LENGTH:
            raise ValueError(f"{path} 文本过长")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} 数值必须是有限数字")
        return value
    if isinstance(value, list):
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise ValueError(f"{path} 数组元素过多")
        return [
            _normalize_bounded_json(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        if len(value) > _MAX_OBJECT_FIELDS:
            raise ValueError(f"{path} 对象字段过多")
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > _MAX_GRAPH_ID_LENGTH:
                raise ValueError(f"{path} 包含不合法的字段名")
            normalized[key] = _normalize_bounded_json(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return normalized
    raise ValueError(f"{path} 包含不支持的数据类型")


def _normalize_graph_identifier(value, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须是字符串")
    identifier = value.strip()
    if not identifier:
        raise ValueError(f"{label} 不能为空")
    if len(identifier) > _MAX_GRAPH_ID_LENGTH:
        raise ValueError(f"{label} 不能超过 {_MAX_GRAPH_ID_LENGTH} 个字符")
    return identifier


def _normalize_position(value, *, label: str) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{label} 的坐标格式错误")
    x = value.get("x")
    y = value.get("y")
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
    ):
        raise ValueError(f"{label} 的坐标必须是数字")
    x_value = float(x)
    y_value = float(y)
    if (
        not math.isfinite(x_value)
        or not math.isfinite(y_value)
        or abs(x_value) > _MAX_LAYOUT_COORDINATE
        or abs(y_value) > _MAX_LAYOUT_COORDINATE
    ):
        raise ValueError(f"{label} 的坐标超出有效范围")
    return {"x": x_value, "y": y_value}


def _normalize_layout(layout_json: dict, topology_json: dict) -> dict:
    if not isinstance(layout_json, dict):
        raise ValueError("layout_json 必须是对象")
    nodes = topology_json.get("nodes", [])
    if len(nodes) > _MAX_LAYOUT_NODES or len(layout_json) > _MAX_LAYOUT_NODES:
        raise ValueError(f"拓扑节点不能超过 {_MAX_LAYOUT_NODES} 个")
    node_ids = {str(node.get("id") or "").strip() for node in nodes}
    if set(layout_json) != node_ids:
        raise ValueError("布局节点与当前拓扑不一致，请重新打开拓扑后重试")
    normalized_layout = {}
    for node_id, position in layout_json.items():
        normalized_position = _normalize_position(position, label=f"节点 {node_id}")
        if normalized_position is None:
            raise ValueError(f"节点 {node_id} 的坐标不能为空")
        normalized_layout[node_id] = normalized_position
    return normalized_layout


def _layout_for_graph(
    nodes: list[dict],
    layout_json: dict | None,
    fallback_layout: dict | None,
) -> dict:
    node_ids = [node["id"] for node in nodes]
    if layout_json is not None:
        return _normalize_layout(layout_json, {"nodes": nodes})

    fallback_layout = fallback_layout if isinstance(fallback_layout, dict) else {}
    layout = {}
    for index, node in enumerate(nodes):
        node_id = node_ids[index]
        position = _normalize_position(node.get("position"), label=f"节点 {node_id}")
        if position is None and node_id in fallback_layout:
            position = _normalize_position(
                fallback_layout[node_id], label=f"节点 {node_id}"
            )
        layout[node_id] = position or {
            "x": float((index % 6) * 300),
            "y": float((index // 6) * 150),
        }
    return layout


def _normalize_graph(
    topology_json: dict,
    layout_json: dict | None = None,
    *,
    fallback_layout: dict | None = None,
) -> tuple[dict, dict]:
    if not isinstance(topology_json, dict):
        raise ValueError("topology_json 必须是对象")
    bounded_graph = _normalize_bounded_json(topology_json, path="topology_json")
    nodes = bounded_graph.get("nodes")
    edges = bounded_graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("topology_json 必须包含 nodes 和 edges 数组")
    if len(nodes) > _MAX_LAYOUT_NODES:
        raise ValueError(f"拓扑节点不能超过 {_MAX_LAYOUT_NODES} 个")
    if len(edges) > _MAX_GRAPH_EDGES:
        raise ValueError(f"拓扑连线不能超过 {_MAX_GRAPH_EDGES} 条")

    normalized_nodes = []
    node_ids = set()
    for index, raw_node in enumerate(nodes):
        if not isinstance(raw_node, dict):
            raise ValueError(f"第 {index + 1} 个拓扑节点必须是对象")
        node = dict(raw_node)
        node_id = _normalize_graph_identifier(
            node.get("id"), label=f"第 {index + 1} 个拓扑节点ID"
        )
        if node_id in node_ids:
            raise ValueError(f"拓扑节点ID重复：{node_id}")
        if "data" in node and not isinstance(node["data"], dict):
            raise ValueError(f"节点 {node_id} 的 data 必须是对象")
        node["id"] = node_id
        node_ids.add(node_id)
        normalized_nodes.append(node)

    normalized_edges = []
    edge_ids = set()
    link_identities = set()
    for index, raw_edge in enumerate(edges):
        if not isinstance(raw_edge, dict):
            raise ValueError(f"第 {index + 1} 条拓扑连线必须是对象")
        edge = dict(raw_edge)
        edge_id = _normalize_graph_identifier(
            edge.get("id"), label=f"第 {index + 1} 条拓扑连线ID"
        )
        source = _normalize_graph_identifier(
            edge.get("source"), label=f"连线 {edge_id} 的 source"
        )
        target = _normalize_graph_identifier(
            edge.get("target"), label=f"连线 {edge_id} 的 target"
        )
        if edge_id in edge_ids:
            raise ValueError(f"拓扑连线ID重复：{edge_id}")
        if source not in node_ids or target not in node_ids:
            raise ValueError(f"连线 {edge_id} 引用了不存在的拓扑节点")
        if source == target:
            raise ValueError(f"连线 {edge_id} 不能连接同一个拓扑节点")
        link_identity = tuple(sorted((source, target)))
        if link_identity in link_identities:
            raise ValueError(f"拓扑连线重复：{source} <-> {target}")
        if (
            "data" in edge
            and edge["data"] is not None
            and not isinstance(edge["data"], dict)
        ):
            raise ValueError(f"连线 {edge_id} 的 data 必须是对象")
        edge.update({"id": edge_id, "source": source, "target": target})
        edge_ids.add(edge_id)
        link_identities.add(link_identity)
        normalized_edges.append(edge)

    normalized_layout = _layout_for_graph(
        normalized_nodes, layout_json, fallback_layout
    )
    for node in normalized_nodes:
        node["position"] = normalized_layout[node["id"]]
    normalized_graph = {"nodes": normalized_nodes, "edges": normalized_edges}
    graph_size = len(orjson.dumps(normalized_graph)) + len(
        orjson.dumps(normalized_layout)
    )
    if graph_size > _MAX_GRAPH_BYTES:
        raise ValueError("拓扑图数据过大")
    return normalized_graph, normalized_layout


def _source_config(data: dict) -> tuple[str, str]:
    sentinel = object()
    raw_config = sentinel
    for key in (
        "config",
        "source_config",
        "device_config",
        "configs",
        "device_configs",
    ):
        if key in data:
            raw_config = data[key]
            break
    if raw_config is sentinel:
        raise ValueError("设备配置不能为空")
    normalized = _normalize_bounded_json(raw_config, path="config")
    if isinstance(normalized, str):
        config_text = normalized.strip()
        canonical = config_text.encode("utf-8")
    else:
        canonical = orjson.dumps(normalized, option=orjson.OPT_SORT_KEYS)
        config_text = orjson.dumps(
            normalized, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS
        ).decode("utf-8")
    if not config_text:
        raise ValueError("设备配置不能为空")
    if len(canonical) > _MAX_SOURCE_CONFIG_BYTES:
        raise ValueError("设备配置数据过大")
    return config_text, hashlib.sha256(canonical).hexdigest()


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


async def _save_graph(request: Request):
    try:
        data = _request_data(request)
        tenant_id = _tenant_id(request)
        raw_id = data.get("id") or data.get("topology_id")
        topology_id = _parse_topology_id(data) if raw_id else None
        name = _parse_topology_name(data)
        topology_json, layout_json = _normalize_graph(
            data.get("topology_json"), data.get("layout_json")
        )
    except ValueError as exc:
        return _err(str(exc))

    await check_permissions(
        request, "network_topology:u" if topology_id else "network_topology:c"
    )
    if not topology_id:
        if await _name_exists(name, tenant_id):
            return _err("拓扑名称已存在")
        topology = await NetworkTopology.create(
            name=name,
            source_config="",
            topology_json=topology_json,
            layout_json=layout_json,
            source_hash=None,
            revision=1,
            last_sync_at=None,
            tenant_id=tenant_id,
        )
        return response_ok(topology.json)

    try:
        expected_revision = _parse_revision(data.get("revision"))
    except ValueError as exc:
        return _err(str(exc))
    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        if topology.revision != expected_revision:
            return _err("拓扑已被更新，请重新打开后保存", status=409)
        if await _name_exists(
            name, tenant_id, exclude_id=topology_id, connection=connection
        ):
            return _err("拓扑名称已存在")
        topology.name = name
        topology.topology_json = topology_json
        topology.layout_json = layout_json
        topology.revision += 1
        await topology.save(
            using_db=connection,
            update_fields=[
                "name",
                "topology_json",
                "layout_json",
                "revision",
                "utime",
            ],
        )
    return response_ok(topology.json)


@topology_bp.post("/manual-save")
@openapi.definition(summary="创建或更新网络拓扑图")
@requires_permission("network_topology:c", "network_topology:u")
async def save_manual_topology(request: Request):
    return await _save_graph(request)


@topology_bp.post("/create")
@openapi.definition(summary="新建网络拓扑")
@requires_permission("network_topology:c")
async def create_topology(request: Request):
    try:
        data = _request_data(request)
        tenant_id = _tenant_id(request)
        name = _parse_topology_name(data)
        topology_json, layout_json = _normalize_graph(
            data.get("topology_json", {"nodes": [], "edges": []}),
            data.get("layout_json", {}),
        )
    except ValueError as exc:
        return _err(str(exc))
    if await _name_exists(name, tenant_id):
        return _err("拓扑名称已存在")
    topology = await NetworkTopology.create(
        name=name,
        source_config="",
        topology_json=topology_json,
        layout_json=layout_json,
        source_hash=None,
        revision=1,
        last_sync_at=None,
        tenant_id=tenant_id,
    )
    return response_ok(topology.json)


@topology_bp.post("/update")
@openapi.definition(summary="更新网络拓扑基础数据")
@requires_permission("network_topology:u")
async def update_topology(request: Request):
    try:
        data = _request_data(request)
        tenant_id = _tenant_id(request)
        topology_id = _parse_topology_id(data)
        expected_revision = _parse_revision(data.get("revision"))
        name = _parse_topology_name(data, required=False)
    except ValueError as exc:
        return _err(str(exc))

    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        if topology.revision != expected_revision:
            return _err("拓扑已被更新，请重新打开后保存", status=409)
        if name and await _name_exists(
            name, tenant_id, exclude_id=topology_id, connection=connection
        ):
            return _err("拓扑名称已存在")

        update_fields = ["revision", "utime"]
        if name is not None:
            topology.name = name
            update_fields.append("name")
        if "topology_json" in data:
            try:
                graph, layout = _normalize_graph(
                    data["topology_json"],
                    data.get("layout_json"),
                    fallback_layout=topology.layout_json,
                )
            except ValueError as exc:
                return _err(str(exc))
            topology.topology_json = graph
            topology.layout_json = layout
            update_fields.extend(["topology_json", "layout_json"])
        elif "layout_json" in data:
            try:
                topology.layout_json = _normalize_layout(
                    data["layout_json"], topology.topology_json or {}
                )
            except ValueError as exc:
                return _err(str(exc))
            update_fields.append("layout_json")
        if len(update_fields) == 2:
            return _err("没有可更新的数据")
        topology.revision += 1
        await topology.save(
            using_db=connection,
            update_fields=list(dict.fromkeys(update_fields)),
        )
    return response_ok(topology.json)


@topology_bp.post("/config-sync")
@openapi.definition(
    summary="写入设备配置并更新网络拓扑",
    description="由剧本在完成设备配置采集后调用；本接口只负责解析、存储和渲染数据。",
)
@requires_permission("network_topology:u")
async def sync_topology_config(request: Request):
    try:
        data = _request_data(request)
        tenant_id = _tenant_id(request)
        topology_id = _parse_topology_id(data)
        expected_revision = _parse_revision(data.get("revision"), required=False)
        config_text, source_hash = _source_config(data)
    except ValueError as exc:
        return _err(str(exc))

    topology = await _tenant_topology(topology_id, tenant_id)
    if not topology:
        return _err("拓扑数据不存在", status=404)
    if expected_revision is not None and topology.revision != expected_revision:
        return _err("拓扑已被更新，请重新获取后同步", status=409)
    if topology.source_hash == source_hash and data.get("force") is not True:
        return response_ok({**topology.json, "changed": False})

    base_revision = topology.revision
    try:
        analyzed_graph = await analyze_topology(config_text, tenant_id=tenant_id)
        topology_json, layout_json = _normalize_graph(
            analyzed_graph, fallback_layout=topology.layout_json
        )
    except (RuntimeError, ValueError) as exc:
        return _err(f"设备配置解析失败：{exc}", status=422)

    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        if topology.revision != base_revision:
            return _err("拓扑在解析期间已被更新，请重试", status=409)
        topology.source_config = config_text
        topology.source_hash = source_hash
        topology.topology_json = topology_json
        topology.layout_json = layout_json
        topology.last_sync_at = datetime.now(TZ)
        topology.revision += 1
        await topology.save(
            using_db=connection,
            update_fields=[
                "source_config",
                "source_hash",
                "topology_json",
                "layout_json",
                "last_sync_at",
                "revision",
                "utime",
            ],
        )
    return response_ok({**topology.json, "changed": True})


@topology_bp.post("/list")
@openapi.definition(summary="获取网络拓扑列表")
@requires_permission("network_topology:r")
async def list_topology(request: Request):
    try:
        data = _request_data(request)
        tenant_id = _tenant_id(request)
        page, page_size = _parse_pagination(data)
    except ValueError as exc:
        return _err(str(exc))
    queryset = NetworkTopology.filter(deleted=False, tenant_id=tenant_id)
    name = str(data.get("name") or "").strip()
    if name:
        queryset = queryset.filter(name__icontains=name)
    total = await queryset.count()
    item_data = await (
        queryset.annotate(
            node_count=RawSQL(
                "CASE WHEN jsonb_typeof(\"topology_json\"->'nodes') = 'array' "
                "THEN jsonb_array_length(\"topology_json\"->'nodes') ELSE 0 END"
            ),
            edge_count=RawSQL(
                "CASE WHEN jsonb_typeof(\"topology_json\"->'edges') = 'array' "
                "THEN jsonb_array_length(\"topology_json\"->'edges') ELSE 0 END"
            ),
        )
        .order_by("-utime", "-ctime")
        .offset((page - 1) * page_size)
        .limit(page_size)
        .values(
            "id",
            "name",
            "node_count",
            "edge_count",
            "revision",
            "last_sync_at",
            "ctime",
            "utime",
        )
    )
    for item in item_data:
        for field in ("last_sync_at", "ctime", "utime"):
            item[field] = strip_timezone_to_seconds(item.get(field))
    return response_ok(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": item_data,
        }
    )


@topology_bp.post("/detail")
@openapi.definition(summary="获取网络拓扑详情")
@requires_permission("network_topology:r")
async def get_topology(request: Request):
    try:
        data = _request_data(request)
        topology_id = _parse_topology_id(data)
        tenant_id = _tenant_id(request)
    except ValueError as exc:
        return _err(str(exc))
    topology = await _tenant_topology(topology_id, tenant_id)
    if not topology:
        return _err("拓扑数据不存在", status=404)
    return response_ok(topology.json)


@topology_bp.post("/layout")
@openapi.definition(summary="保存网络拓扑节点布局")
@requires_permission("network_topology:u")
async def update_layout(request: Request):
    try:
        data = _request_data(request)
        topology_id = _parse_topology_id(data)
        tenant_id = _tenant_id(request)
        if "layout_json" not in data:
            raise ValueError("layout_json 不能为空")
        expected_revision = _parse_revision(data.get("revision"), required=False)
    except ValueError as exc:
        return _err(str(exc))
    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        if expected_revision is not None and topology.revision != expected_revision:
            return _err("拓扑已被更新，请重新打开后保存布局", status=409)
        if "source_hash" in data and (data.get("source_hash") or "") != (
            topology.source_hash or ""
        ):
            return _err("拓扑配置已更新，请重新打开后保存布局", status=409)
        try:
            topology.layout_json = _normalize_layout(
                data["layout_json"], topology.topology_json or {}
            )
        except ValueError as exc:
            return _err(str(exc))
        topology.revision += 1
        await topology.save(
            using_db=connection,
            update_fields=["layout_json", "revision", "utime"],
        )
    return response_ok(
        {
            "id": topology.id,
            "revision": topology.revision,
            "source_hash": topology.source_hash,
        }
    )


@topology_bp.post("/delete")
@openapi.definition(summary="删除网络拓扑")
@requires_permission("network_topology:d")
async def delete_topology(request: Request):
    try:
        data = _request_data(request)
        topology_id = _parse_topology_id(data)
        tenant_id = _tenant_id(request)
    except ValueError as exc:
        return _err(str(exc))
    async with in_transaction() as connection:
        topology = await _tenant_topology(
            topology_id, tenant_id, connection=connection, lock=True
        )
        if not topology:
            return _err("拓扑数据不存在", status=404)
        topology.deleted = True
        topology.revision += 1
        await topology.save(
            using_db=connection,
            update_fields=["deleted", "revision", "utime"],
        )
    return response_ok({"id": topology.id})
