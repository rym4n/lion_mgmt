"""Bounded parsing and normalization for legacy topology graph requests."""

import hashlib
import math

import orjson
from sanic.request import Request

from services.network_topology.view_helpers import _request_data, _tenant_id


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
_LEGACY_CONFIG_FIELDS = frozenset(
    {"config", "source_config", "device_config", "configs", "device_configs"}
)
_SNAPSHOT_SCHEMA = "aioe.network-topology.collection/v2"


def _config_sync_shape(data: dict) -> str:
    has_snapshot = "snapshot" in data
    has_legacy = bool(_LEGACY_CONFIG_FIELDS.intersection(data))
    if has_snapshot and has_legacy:
        raise ValueError("snapshot 与 legacy 配置字段不能同时提交")
    return "snapshot" if has_snapshot else "legacy"


def _validate_snapshot_shape(data: dict) -> None:
    snapshot = data.get("snapshot")
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != _SNAPSHOT_SCHEMA
    ):
        raise ValueError("snapshot schema_version 不合法")


def _parse_layout_request(request: Request) -> tuple[dict, str, str, int | None]:
    data = _request_data(request)
    topology_id = _parse_topology_id(data)
    tenant_id = _tenant_id(request)
    if "layout_json" not in data:
        raise ValueError("layout_json 不能为空")
    revision = _parse_revision(data.get("revision"), required=False)
    return data, topology_id, tenant_id, revision


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
            _normalize_bounded_json(item, path=f"{path}[{index}]", depth=depth + 1)
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
                item, path=f"{path}.{key}", depth=depth + 1
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
    nodes: list[dict], layout_json: dict | None, fallback_layout: dict | None
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
    graph_size = len(orjson.dumps(normalized_graph)) + len(orjson.dumps(normalized_layout))
    if graph_size > _MAX_GRAPH_BYTES:
        raise ValueError("拓扑图数据过大")
    return normalized_graph, normalized_layout


def _require_topology_node(topology_json: dict) -> None:
    if not topology_json["nodes"]:
        raise ValueError("拓扑图至少需要一个节点")


def _source_config(data: dict) -> tuple[str, str]:
    sentinel = object()
    raw_config = sentinel
    for key in ("config", "source_config", "device_config", "configs", "device_configs"):
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
