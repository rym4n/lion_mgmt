# coding=utf-8
from datetime import datetime

from sanic import Blueprint, Request
from sanic_ext import openapi
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.command_center import CommandAgent
from settings import TZ

agent_bp = Blueprint("agent", url_prefix="/agent")

AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "智能体ID"},
        "ctime": {"type": "string", "description": "创建时间"},
        "utime": {"type": "string", "description": "更新时间"},
        "agent_key": {"type": "string", "description": "智能体逻辑标识"},
        "name": {"type": "string", "description": "智能体名称"},
        "description": {"type": "string", "nullable": True, "description": "智能体说明"},
        "capabilities": {"type": "array", "items": {"type": "string"}, "description": "能力标签列表"},
        "status": {"type": "string", "description": "状态: idle,busy,offline,error"},
        "load": {"type": "integer", "description": "当前任务负载"},
        "max_concurrent": {"type": "integer", "description": "最大并发任务数"},
        "current_task_id": {"type": "string", "nullable": True, "description": "当前主任务ID"},
        "heartbeat_at": {"type": "string", "nullable": True, "description": "最近心跳时间"},
        "metadata": {"type": "object", "description": "扩展元数据"},
        "runtime": {"type": "string", "description": "运行时类型，默认nanobot"},
        "system_instruction": {"type": "string", "nullable": True, "description": "调用Nanobot时使用的system_instruction提示词"},
        "prompt_config": {"type": "object", "description": "提示词扩展配置"},
        "is_builtin": {"type": "boolean", "description": "是否为内置智能体"},
    },
}

ERROR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "integer", "description": "错误码"},
        "message": {"type": "string", "description": "错误信息"},
        "data": {"type": "object", "nullable": True, "description": "错误详情"},
    },
}


def _json_body(schema: dict) -> dict:
    return {"application/json": {"schema": schema}}


def _response(description: str, data_schema: dict, errors: dict[str, str] | None = None) -> dict:
    response = {
        "200": {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "description": "业务状态码"},
                            "message": {"type": "string", "description": "响应信息"},
                            "data": data_schema,
                        },
                    }
                }
            },
        }
    }
    for status, error_description in (errors or {}).items():
        response[str(status)] = {
            "description": error_description,
            "content": {"application/json": {"schema": ERROR_RESPONSE_SCHEMA}},
        }
    return response


def _require_json(request: Request) -> dict:
    return request.json or {}


def _session_user_id(request: Request) -> str | None:
    account_info = request.ctx.session.get("account_info")
    if isinstance(account_info, dict):
        return account_info.get("id")
    return getattr(account_info, "id", None)


def _normalize_string_list(value, field_name: str) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise CustomError(ErrorCode.PARAM_ERROR)
    result = []
    for item in value:
        normalized = str(item).strip()
        if normalized:
            result.append(normalized)
    return result


def _normalize_dict(value, field_name: str) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return value


async def _get_agent(tenant_id: str, agent_id: str | None = None, agent_key: str | None = None) -> CommandAgent | None:
    if agent_id:
        return await CommandAgent.get_or_none(id=agent_id, tenant_id=tenant_id, deleted=False)
    if agent_key:
        return await CommandAgent.get_or_none(agent_key=agent_key, tenant_id=tenant_id, deleted=False)
    return None


async def disable_command_agent(agent: CommandAgent) -> None:
    agent.status = "offline"
    agent.load = 0
    agent.current_task_id = None
    agent.heartbeat_at = datetime.now(TZ)
    await agent.save(update_fields=["status", "load", "current_task_id", "heartbeat_at", "utime"])


def _agent_payload(data: dict, *, partial: bool = False) -> dict:
    payload = {}
    string_fields = ["name", "description", "status", "runtime", "system_instruction"]
    for field in string_fields:
        if field in data:
            payload[field] = data[field]

    if "capabilities" in data:
        payload["capabilities"] = _normalize_string_list(data.get("capabilities"), "capabilities")
    elif not partial:
        payload["capabilities"] = []

    if "metadata" in data:
        payload["metadata"] = _normalize_dict(data.get("metadata"), "metadata")
    elif not partial:
        payload["metadata"] = {}

    if "prompt_config" in data:
        payload["prompt_config"] = _normalize_dict(data.get("prompt_config"), "prompt_config")
    elif not partial:
        payload["prompt_config"] = {}

    if "max_concurrent" in data:
        payload["max_concurrent"] = max(1, int(data.get("max_concurrent") or 1))
    elif not partial:
        payload["max_concurrent"] = 1

    if "is_builtin" in data:
        payload["is_builtin"] = bool(data.get("is_builtin"))
    elif not partial:
        payload["is_builtin"] = False

    if not partial:
        payload.setdefault("status", "idle")
        payload.setdefault("runtime", "nanobot")

    return payload


CREATE_AGENT_BODY = {
    "type": "object",
    "required": ["agent_key", "name"],
    "properties": {
        "agent_key": {"type": "string", "description": "智能体逻辑标识，同一租户内唯一"},
        "name": {"type": "string", "description": "智能体名称"},
        "description": {"type": "string", "description": "智能体说明"},
        "capabilities": {"type": "array", "items": {"type": "string"}, "description": "能力标签列表，指挥中心按此调度"},
        "system_instruction": {"type": "string", "description": "调用Nanobot时注入的system_instruction提示词"},
        "prompt_config": {"type": "object", "description": "提示词扩展配置，如变量、模板来源等"},
        "is_builtin": {"type": "boolean", "description": "是否为内置智能体，默认false"},
        "runtime": {"type": "string", "description": "运行时类型，默认nanobot"},
        "metadata": {"type": "object", "description": "扩展元数据"},
        "status": {"type": "string", "description": "状态: idle,busy,offline,error", "default": "idle"},
        "max_concurrent": {"type": "integer", "minimum": 1, "description": "最大并发任务数"},
    },
}


@agent_bp.post("/create")
@openapi.definition(
    summary="创建智能体",
    description="创建一个可被指挥中心调度的Nanobot智能体配置。数据落在command_center_agent表。需要agent:c权限。",
    body=_json_body(CREATE_AGENT_BODY),
    response=_response("创建成功", AGENT_SCHEMA, {"400": "参数错误", "409": "智能体已存在"}),
)
@requires_permission("agent:c")
async def create_agent(request: Request):
    data = _require_json(request)
    agent_key = str(data.get("agent_key") or "").strip()
    name = str(data.get("name") or "").strip()
    if not agent_key or not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    exists = await CommandAgent.get_or_none(tenant_id=tenant_id, agent_key=agent_key, deleted=False)
    if exists:
        raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)

    payload = _agent_payload(data)
    payload["agent_key"] = agent_key
    payload["name"] = name
    agent = await CommandAgent.create(tenant_id=tenant_id, **payload)
    return response_ok(agent.json)


@agent_bp.post("/list")
@openapi.definition(
    summary="查询智能体列表",
    description="分页查询智能体配置，可按状态、内置标识、运行时和关键字过滤。需要agent:r权限。",
    body=_json_body(
        {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "size": {"type": "integer", "minimum": 1, "default": 15},
                "status": {"type": "string", "description": "状态过滤"},
                "runtime": {"type": "string", "description": "运行时过滤"},
                "is_builtin": {"type": "boolean", "description": "是否内置过滤"},
                "keyword": {"type": "string", "description": "按名称、标识、说明模糊搜索"},
            },
        }
    ),
    response=_response(
        "查询成功",
        {
            "type": "object",
            "properties": {
                "total": {"type": "integer"},
                "page": {"type": "integer"},
                "size": {"type": "integer"},
                "data": {"type": "array", "items": AGENT_SCHEMA},
            },
        },
    ),
)
@requires_permission("agent:r")
async def list_agents(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    page = max(1, int(data.get("page", 1) or 1))
    size = max(1, min(int(data.get("size", 15) or 15), 100))

    condition = Q(tenant_id=tenant_id, deleted=False)
    if data.get("status"):
        condition &= Q(status=data["status"])
    if data.get("runtime"):
        condition &= Q(runtime=data["runtime"])
    if "is_builtin" in data:
        condition &= Q(is_builtin=bool(data.get("is_builtin")))
    if data.get("keyword"):
        keyword = data["keyword"]
        condition &= Q(agent_key__icontains=keyword) | Q(name__icontains=keyword) | Q(description__icontains=keyword)

    queryset = CommandAgent.filter(condition)
    total = await queryset.count()
    agents = await queryset.order_by("-is_builtin", "status", "agent_key").offset((page - 1) * size).limit(size).all()
    return response_ok({"total": total, "page": page, "size": size, "data": [item.json for item in agents]})


@agent_bp.post("/detail")
@openapi.definition(
    summary="查询智能体详情",
    description="按id或agent_key查询智能体详情。需要agent:r权限。",
    body=_json_body(
        {
            "type": "object",
            "oneOf": [{"required": ["id"]}, {"required": ["agent_key"]}],
            "properties": {
                "id": {"type": "string", "description": "智能体ID"},
                "agent_key": {"type": "string", "description": "智能体逻辑标识"},
            },
        }
    ),
    response=_response("查询成功", AGENT_SCHEMA, {"400": "参数错误", "404": "智能体不存在"}),
)
@requires_permission("agent:r")
async def detail_agent(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    agent = await _get_agent(tenant_id, data.get("id"), data.get("agent_key"))
    if not agent:
        raise CustomError(ErrorCode.NOT_FOUND)
    return response_ok(agent.json)


@agent_bp.post("/update")
@openapi.definition(
    summary="更新智能体",
    description="更新智能体基础信息、能力标签、Nanobot system_instruction和提示词配置。内置智能体不可改为非内置。需要agent:u权限。",
    body=_json_body(
        {
            "type": "object",
            "oneOf": [{"required": ["id"]}, {"required": ["agent_key"]}],
            "properties": {**CREATE_AGENT_BODY["properties"], "id": {"type": "string", "description": "智能体ID"}},
        }
    ),
    response=_response("更新成功", AGENT_SCHEMA, {"400": "参数错误", "404": "智能体不存在"}),
)
@requires_permission("agent:u")
async def update_agent(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    agent = await _get_agent(tenant_id, data.get("id"), data.get("agent_key"))
    if not agent:
        raise CustomError(ErrorCode.NOT_FOUND)

    payload = _agent_payload(data, partial=True)
    if agent.is_builtin and payload.get("is_builtin") is False:
        raise CustomError(ErrorCode.INTERNAL_DELETE_ERROR)
    if "agent_key" in data and data.get("agent_key") and data.get("agent_key") != agent.agent_key:
        new_key = str(data["agent_key"]).strip()
        exists = await CommandAgent.get_or_none(tenant_id=tenant_id, agent_key=new_key, deleted=False)
        if exists and exists.id != agent.id:
            raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
        payload["agent_key"] = new_key

    for field, value in payload.items():
        setattr(agent, field, value)
    await agent.save()
    return response_ok(agent.json)


@agent_bp.post("/delete")
@openapi.definition(
    summary="删除智能体",
    description="软删除人工创建的智能体，并同步将指挥中心调度状态置为offline。内置智能体禁止删除。需要agent:d权限。",
    body=_json_body(
        {
            "type": "object",
            "oneOf": [{"required": ["id"]}, {"required": ["agent_key"]}],
            "properties": {
                "id": {"type": "string", "description": "智能体ID"},
                "agent_key": {"type": "string", "description": "智能体逻辑标识"},
            },
        }
    ),
    response=_response("删除成功", {"type": "object"}, {"400": "参数错误", "404": "智能体不存在"}),
)
@requires_permission("agent:d")
async def delete_agent(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    agent = await _get_agent(tenant_id, data.get("id"), data.get("agent_key"))
    if not agent:
        raise CustomError(ErrorCode.NOT_FOUND)
    if agent.is_builtin:
        raise CustomError(ErrorCode.INTERNAL_DELETE_ERROR)

    await disable_command_agent(agent)
    agent.deleted = True
    await agent.save(update_fields=["deleted", "utime"])
    return response_ok({"id": agent.id, "deleted": True})


__all__ = ["agent_bp"]
