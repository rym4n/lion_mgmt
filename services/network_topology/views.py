# coding=utf-8

import uuid

import orjson
from sanic import Blueprint, json as sanic_json
from sanic.request import Request
from sanic.response import ResponseStream
from sanic_ext import openapi

from core.response import response_ok
from libs.security.authorization import requires_permission
from models.network_topology import NetworkTopology
from services.network_topology.analyzer import analyze_topology_stream

topology_bp = Blueprint("network_topology", url_prefix="/network-topology")
__all__ = ["topology_bp"]

_MAX_CONFIG_SIZE = 5 * 1024 * 1024  # 5 MB


def _err(message: str, status: int = 400):
    return sanic_json({"code": 4000, "message": message}, status=status, dumps=orjson.dumps)


@topology_bp.post("/parse")
@openapi.definition(
    summary="上传设备配置并流式解析网络拓扑（SSE）",
    description=(
        "接收设备配置文本，调用 Nanobot 解析为网络拓扑结构。\n"
        "响应为 Server-Sent Events 流，前端需以 EventSource 或 fetch+ReadableStream 接收。\n\n"
        "事件格式（每条 `data: <json>`）：\n"
        "- `{\"type\": \"progress\", \"message\": \"...\"}` — 解析进度文字\n"
        "- `{\"type\": \"token\", \"content\": \"...\"}` — Nanobot 输出片段\n"
        "- `{\"type\": \"result\", \"topology\": {...}}` — 最终拓扑 JSON\n"
        "- `{\"type\": \"error\", \"message\": \"...\"}` — 错误信息"
    ),
    body={
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "config": {"type": "string", "format": "binary", "description": "设备配置文件（纯文本，≤5MB）"},
                    "config_text": {"type": "string", "description": "直接粘贴的配置文本（与 config 文件二选一）"},
                    "session_id": {"type": "string", "description": "Nanobot 会话 ID（可选，用于保持上下文）"},
                },
            }
        }
    },
)
@requires_permission("network_topology:r")
async def parse_topology(request: Request):
    config_text = None

    if request.files and "config" in request.files:
        file = request.files["config"][0]
        if len(file.body) > _MAX_CONFIG_SIZE:
            return _err("配置文件超过 5MB 限制，请压缩后重试")
        config_text = file.body.decode("utf-8", errors="replace")
    elif request.form and request.form.get("config_text"):
        config_text = request.form.get("config_text")
        if len(config_text.encode("utf-8")) > _MAX_CONFIG_SIZE:
            return _err("配置文本超过 5MB 限制，请精简后重试")
    elif request.json and request.json.get("config_text"):
        config_text = request.json.get("config_text")
        if len(config_text.encode("utf-8")) > _MAX_CONFIG_SIZE:
            return _err("配置文本超过 5MB 限制，请精简后重试")

    if not config_text or not config_text.strip():
        return _err("请提供设备配置文本或上传配置文件")

    tenant_id = request.ctx.session.get("tenant_id")
    session_id = (
        (request.form.get("session_id") if request.form else None)
        or ((request.json or {}).get("session_id") if request.json else None)
        or f"network-topology:{uuid.uuid4()}"
    )

    async def _stream_response(response):
        async for chunk in analyze_topology_stream(config_text, tenant_id=tenant_id, session_id=session_id):
            await response.write(chunk)

    return ResponseStream(
        _stream_response,
        status=200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@topology_bp.post("/save")
@openapi.definition(
    summary="保存拓扑快照",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "source_config", "topology_json"],
                "properties": {
                    "name": {"type": "string", "description": "拓扑名称"},
                    "source_config": {"type": "string", "description": "原始配置文本"},
                    "topology_json": {"type": "object", "description": "拓扑结构 JSON"},
                    "layout_json": {"type": "object", "description": "节点布局（可选）"},
                },
            }
        }
    },
)
@requires_permission("network_topology:w")
async def save_topology(request: Request):
    data = request.json or {}
    name = data.get("name", "").strip()
    source_config = data.get("source_config", "").strip()
    topology_json = data.get("topology_json")
    layout_json = data.get("layout_json")

    if not name:
        return _err("拓扑名称不能为空")
    if not topology_json:
        return _err("拓扑结构数据不能为空")

    tenant_id = request.ctx.session.get("tenant_id")

    topology = await NetworkTopology.create(
        name=name,
        source_config=source_config,
        topology_json=topology_json,
        layout_json=layout_json,
        tenant_id=tenant_id,
    )

    return response_ok(topology.json)


@topology_bp.post("/list")
@openapi.definition(
    summary="获取拓扑快照列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1},
                    "page_size": {"type": "integer", "default": 20},
                },
            }
        }
    },
)
@requires_permission("network_topology:r")
async def list_topology(request: Request):
    data = request.json or {}
    page = max(int(data.get("page", 1)), 1)
    page_size = min(max(int(data.get("page_size", 20)), 1), 100)
    tenant_id = request.ctx.session.get("tenant_id")

    qs = NetworkTopology.filter(deleted=False, tenant_id=tenant_id).order_by("-ctime")
    total = await qs.count()
    items = await qs.offset((page - 1) * page_size).limit(page_size).all()

    return response_ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "node_count": len((t.topology_json or {}).get("nodes", [])),
                "edge_count": len((t.topology_json or {}).get("edges", [])),
                "ctime": str(t.ctime),
                "utime": str(t.utime),
            }
            for t in items
        ],
    })


@topology_bp.post("/detail")
@openapi.definition(
    summary="获取拓扑快照详情",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "拓扑快照ID"}
                },
            }
        }
    },
)
@requires_permission("network_topology:r")
async def get_topology(request: Request):
    data = request.json or {}
    topology_id = data.get("id") or data.get("topology_id")
    if not topology_id:
        return _err("拓扑ID不能为空")

    tenant_id = request.ctx.session.get("tenant_id")
    topology = await NetworkTopology.get_or_none(id=topology_id, deleted=False, tenant_id=tenant_id)
    if not topology:
        return _err("拓扑数据不存在", status=404)
    return response_ok(topology.json)


@topology_bp.post("/layout")
@openapi.definition(
    summary="更新拓扑节点布局",
    description="前端拖拽节点后，将新布局数据保存到后端",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id", "layout_json"],
                "properties": {
                    "id": {"type": "string", "description": "拓扑快照ID"},
                    "layout_json": {"type": "object", "description": "节点坐标布局数据"}
                },
            }
        }
    },
)
@requires_permission("network_topology:w")
async def update_layout(request: Request):
    data = request.json or {}
    topology_id = data.get("id") or data.get("topology_id")
    if not topology_id:
        return _err("拓扑ID不能为空")

    tenant_id = request.ctx.session.get("tenant_id")
    topology = await NetworkTopology.get_or_none(id=topology_id, deleted=False, tenant_id=tenant_id)
    if not topology:
        return _err("拓扑数据不存在", status=404)

    layout_json = data.get("layout_json")
    if layout_json is None:
        return _err("layout_json 不能为空")

    topology.layout_json = layout_json
    await topology.save(update_fields=["layout_json", "utime"])
    return response_ok({"id": topology_id})


@topology_bp.post("/delete")
@openapi.definition(
    summary="删除拓扑快照（软删除）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "拓扑快照ID"}
                },
            }
        }
    },
)
@requires_permission("network_topology:w")
async def delete_topology(request: Request):
    data = request.json or {}
    topology_id = data.get("id") or data.get("topology_id")
    if not topology_id:
        return _err("拓扑ID不能为空")

    tenant_id = request.ctx.session.get("tenant_id")
    topology = await NetworkTopology.get_or_none(id=topology_id, deleted=False, tenant_id=tenant_id)
    if not topology:
        return _err("拓扑数据不存在", status=404)

    topology.deleted = True
    await topology.save(update_fields=["deleted", "utime"])
    return response_ok({"id": topology_id})
