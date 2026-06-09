# coding=utf-8
from datetime import datetime

from sanic import Blueprint, Request
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.command_center import CommandAgent, CommandTask, CommandTaskEvent
from settings import TZ

from .manager import create_command_task, dispatch_task, list_tasks, update_task_progress

command_bp = Blueprint("command_center", url_prefix="/command_center")
__all__ = ["command_bp"]

ERROR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "integer", "description": "错误码"},
        "message": {"type": "string", "description": "错误信息"},
        "data": {"type": "object", "nullable": True, "description": "错误详情"},
    },
}

AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "智能体ID"},
        "ctime": {"type": "string", "description": "创建时间"},
        "utime": {"type": "string", "description": "更新时间"},
        "agent_key": {"type": "string", "description": "智能体逻辑标识"},
        "name": {"type": "string", "description": "智能体名称"},
        "description": {"type": "string", "nullable": True, "description": "智能体说明"},
        "capabilities": {
            "type": "array",
            "description": "能力标签列表，用于任务调度匹配",
            "items": {"type": "string"},
        },
        "status": {
            "type": "string",
            "description": "智能体状态",
            "enum": ["idle", "busy", "offline", "error"],
        },
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

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "任务ID"},
        "ctime": {"type": "string", "description": "创建时间"},
        "utime": {"type": "string", "description": "更新时间"},
        "title": {"type": "string", "description": "任务标题"},
        "description": {"type": "string", "nullable": True, "description": "任务描述"},
        "source": {"type": "string", "description": "任务来源"},
        "priority": {"type": "integer", "description": "优先级，数值越小优先级越高"},
        "status": {
            "type": "string",
            "description": "任务状态",
            "enum": ["pending_dispatch", "dispatched", "running", "completed", "failed", "cancelled"],
        },
        "progress": {"type": "integer", "description": "任务进度，范围0-100", "minimum": 0, "maximum": 100},
        "required_capabilities": {
            "type": "array",
            "description": "任务所需能力标签",
            "items": {"type": "string"},
        },
        "payload": {"type": "object", "description": "任务载荷"},
        "requested_agent_id": {"type": "string", "nullable": True, "description": "用户指定的智能体ID或agent_key"},
        "assigned_agent_id": {"type": "string", "nullable": True, "description": "实际分配的智能体ID"},
        "assigned_agent_key": {"type": "string", "nullable": True, "description": "实际分配的智能体逻辑标识"},
        "dispatch_status": {
            "type": "string",
            "description": "调度状态",
            "enum": ["pending", "user_specified", "corrected", "auto_assigned", "no_candidate"],
        },
        "dispatch_reason": {"type": "string", "nullable": True, "description": "调度原因"},
        "run_id": {"type": "string", "nullable": True, "description": "后续执行系统run_id"},
        "room_id": {"type": "string", "nullable": True, "description": "关联作战室ID"},
        "creator_id": {"type": "string", "nullable": True, "description": "创建人ID"},
        "started_at": {"type": "string", "nullable": True, "description": "开始时间"},
        "finished_at": {"type": "string", "nullable": True, "description": "结束时间"},
    },
}

TASK_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "事件ID"},
        "ctime": {"type": "string", "description": "创建时间"},
        "utime": {"type": "string", "description": "更新时间"},
        "task_id": {"type": "string", "description": "任务ID"},
        "event_type": {
            "type": "string",
            "description": "事件类型，如task_created、task_dispatched、dispatch_waiting、task_progress_updated",
        },
        "status": {"type": "string", "nullable": True, "description": "任务状态快照"},
        "progress": {"type": "integer", "nullable": True, "description": "进度快照"},
        "agent_key": {"type": "string", "nullable": True, "description": "相关智能体"},
        "message": {"type": "string", "nullable": True, "description": "事件说明"},
        "metadata": {"type": "object", "description": "事件元数据"},
        "operator_id": {"type": "string", "nullable": True, "description": "操作者ID"},
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


@command_bp.post("/agents/register")
@openapi.definition(
    summary="登记或更新指挥部智能体画像",
    description="登记新的指挥部智能体，或按tenant_id + agent_key更新已有智能体画像、能力标签、状态和心跳时间。需要command_center:u权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["agent_key"],
            "properties": {
                "agent_key": {"type": "string", "description": "智能体逻辑标识，同一租户内唯一"},
                "name": {"type": "string", "description": "智能体名称；未传时默认使用agent_key"},
                "description": {"type": "string", "description": "智能体说明"},
                "capabilities": {
                    "type": "array",
                    "description": "能力标签列表，用于任务required_capabilities匹配",
                    "items": {"type": "string"},
                },
                "status": {
                    "type": "string",
                    "description": "智能体状态",
                    "enum": ["idle", "busy", "offline", "error"],
                    "default": "idle",
                },
                "max_concurrent": {"type": "integer", "description": "最大并发任务数", "minimum": 1, "default": 1},
                "metadata": {"type": "object", "description": "扩展元数据"},
                "runtime": {"type": "string", "description": "运行时类型，默认nanobot"},
                "system_instruction": {"type": "string", "description": "调用Nanobot时使用的system_instruction提示词"},
                "prompt_config": {"type": "object", "description": "提示词扩展配置"},
                "is_builtin": {"type": "boolean", "description": "是否为内置智能体"},
            },
        }
    ),
    response=_response("登记或更新成功", AGENT_SCHEMA, {"400": "参数错误"}),
)
@requires_permission("command_center:u")
async def register_agent(request: Request):
    data = _require_json(request)
    agent_key = data.get("agent_key")
    name = data.get("name") or agent_key
    if not agent_key:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    agent, _ = await CommandAgent.get_or_create(
        tenant_id=tenant_id,
        agent_key=agent_key,
        defaults={
            "name": name,
            "description": data.get("description"),
            "capabilities": data.get("capabilities", []),
            "status": data.get("status", "idle"),
            "max_concurrent": data.get("max_concurrent", 1),
            "metadata": data.get("metadata", {}),
            "runtime": data.get("runtime", "nanobot"),
            "system_instruction": data.get("system_instruction"),
            "prompt_config": data.get("prompt_config", {}),
            "is_builtin": data.get("is_builtin", False),
        },
    )
    for field in ["name", "description", "capabilities", "status", "max_concurrent", "metadata", "runtime", "system_instruction", "prompt_config", "is_builtin"]:
        if field in data:
            setattr(agent, field, data[field])
    agent.deleted = False
    agent.heartbeat_at = datetime.now(TZ)
    await agent.save()
    return response_ok(agent.json)


@command_bp.post("/agents/list")
@openapi.definition(
    summary="查看智能体工作状态",
    description="按租户查询未删除的指挥部智能体，可按状态过滤，并按status、load、agent_key排序。需要command_center:r权限。",
    body=_json_body(
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "可选，按智能体状态过滤",
                    "enum": ["idle", "busy", "offline", "error"],
                }
            },
        }
    ),
    response=_response(
        "查询成功",
        {
            "type": "object",
            "properties": {
                "agents": {"type": "array", "description": "智能体列表", "items": AGENT_SCHEMA},
            },
        },
    ),
)
@requires_permission("command_center:r")
async def list_agents(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    condition = {"tenant_id": tenant_id, "deleted": False}
    if data.get("status"):
        condition["status"] = data["status"]
    agents = await CommandAgent.filter(**condition).order_by("status", "load", "agent_key").all()
    return response_ok({"agents": [item.json for item in agents]})


@command_bp.post("/agents/status")
@openapi.definition(
    summary="更新智能体工作状态",
    description="按id或agent_key定位智能体，更新状态、负载、并发上限、当前任务或元数据，并刷新心跳时间。需要command_center:u权限。",
    body=_json_body(
        {
            "type": "object",
            "oneOf": [{"required": ["id"]}, {"required": ["agent_key"]}],
            "properties": {
                "id": {"type": "string", "description": "智能体ID，与agent_key二选一"},
                "agent_key": {"type": "string", "description": "智能体逻辑标识，与id二选一"},
                "status": {
                    "type": "string",
                    "description": "智能体状态",
                    "enum": ["idle", "busy", "offline", "error"],
                },
                "load": {"type": "integer", "description": "当前任务负载", "minimum": 0},
                "max_concurrent": {"type": "integer", "description": "最大并发任务数", "minimum": 1},
                "current_task_id": {"type": "string", "nullable": True, "description": "当前主任务ID"},
                "metadata": {"type": "object", "description": "扩展元数据"},
                "runtime": {"type": "string", "description": "运行时类型"},
                "system_instruction": {"type": "string", "description": "调用Nanobot时使用的system_instruction提示词"},
                "prompt_config": {"type": "object", "description": "提示词扩展配置"},
                "is_builtin": {"type": "boolean", "description": "是否为内置智能体"},
            },
        }
    ),
    response=_response("更新成功", AGENT_SCHEMA, {"400": "参数错误", "404": "智能体不存在"}),
)
@requires_permission("command_center:u")
async def update_agent_status(request: Request):
    data = _require_json(request)
    agent_id = data.get("id") or data.get("agent_key")
    if not agent_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    if len(agent_id) == 36:
        agent = await CommandAgent.get_or_none(id=agent_id, tenant_id=tenant_id, deleted=False)
    else:
        agent = await CommandAgent.get_or_none(agent_key=agent_id, tenant_id=tenant_id, deleted=False)
    if not agent:
        raise CustomError(ErrorCode.NOT_FOUND)

    for field in ["status", "load", "max_concurrent", "current_task_id", "metadata", "runtime", "system_instruction", "prompt_config", "is_builtin"]:
        if field in data:
            setattr(agent, field, data[field])
    agent.heartbeat_at = datetime.now(TZ)
    await agent.save()
    return response_ok(agent.json)


@command_bp.post("/tasks/create")
@openapi.definition(
    summary="创建指挥部任务并触发调度",
    description="创建指挥部任务后立即按required_capabilities触发调度；如指定agent_id但能力或状态不匹配，会自动纠正到可用智能体。需要command_center:c权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string", "description": "任务标题"},
                "description": {"type": "string", "description": "任务描述"},
                "source": {"type": "string", "description": "任务来源", "default": "manual"},
                "priority": {"type": "integer", "description": "优先级，数值越小优先级越高", "default": 3},
                "required_capabilities": {
                    "type": "array",
                    "description": "任务所需能力标签；候选智能体必须完整覆盖",
                    "items": {"type": "string"},
                },
                "payload": {"type": "object", "description": "任务载荷，后续执行系统可读取"},
                "agent_id": {"type": "string", "description": "可选，用户指定的智能体ID或agent_key"},
                "requested_agent_id": {"type": "string", "description": "可选，用户指定的智能体ID或agent_key"},
                "room_id": {"type": "string", "description": "可选，关联作战室ID"},
            },
        }
    ),
    response=_response("创建并调度成功", TASK_SCHEMA, {"400": "参数错误"}),
)
@requires_permission("command_center:c")
async def create_task(request: Request):
    data = _require_json(request)
    if not data.get("title"):
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    creator = request.ctx.session["account_info"]
    task = await create_command_task(tenant_id, creator, data)
    return response_ok(task.json)


@command_bp.post("/tasks/list")
@openapi.definition(
    summary="查看任务列表与进展",
    description="分页查询指挥部任务，可按任务状态、实际分配智能体和关键字过滤。需要command_center:r权限。",
    body=_json_body(
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "可选，按任务状态过滤",
                    "enum": ["pending_dispatch", "dispatched", "running", "completed", "failed", "cancelled"],
                },
                "agent_key": {"type": "string", "description": "可选，按实际分配的智能体agent_key过滤"},
                "keyword": {"type": "string", "description": "可选，按任务标题或描述模糊搜索"},
                "page": {"type": "integer", "description": "页码", "minimum": 1, "default": 1},
                "size": {"type": "integer", "description": "每页数量", "minimum": 1, "default": 20},
            },
        }
    ),
    response=_response(
        "查询成功",
        {
            "type": "object",
            "properties": {
                "total": {"type": "integer", "description": "总数"},
                "page": {"type": "integer", "description": "当前页码"},
                "size": {"type": "integer", "description": "每页数量"},
                "tasks": {"type": "array", "description": "任务列表", "items": TASK_SCHEMA},
            },
        },
    ),
)
@requires_permission("command_center:r")
async def query_tasks(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    tasks, total = await list_tasks(tenant_id, data)
    return response_ok(
        {
            "total": total,
            "page": data.get("page", 1),
            "size": data.get("size", 20),
            "tasks": [task.json for task in tasks],
        }
    )


@command_bp.post("/tasks/detail")
@openapi.definition(
    summary="查看任务详情和事件流",
    description="查询单个任务详情，并按创建时间返回任务事件流，用于展示调度历史和进度变化。需要command_center:r权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "任务ID"},
            },
        }
    ),
    response=_response(
        "查询成功",
        {
            "type": "object",
            "properties": {
                "task": TASK_SCHEMA,
                "events": {"type": "array", "description": "任务事件流", "items": TASK_EVENT_SCHEMA},
            },
        },
        {"400": "参数错误", "404": "任务不存在"},
    ),
)
@requires_permission("command_center:r")
async def task_detail(request: Request):
    data = _require_json(request)
    task_id = data.get("id")
    if not task_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)
    events = await CommandTaskEvent.filter(task_id=task.id, tenant_id=tenant_id, deleted=False).order_by("ctime").all()
    return response_ok({"task": task.json, "events": [event.json for event in events]})


@command_bp.post("/tasks/progress")
@openapi.definition(
    summary="更新任务进度或状态",
    description="更新任务进度、状态或后续执行系统run_id，并记录任务事件；任务完成、失败或取消时会释放智能体负载。需要command_center:u权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "任务ID"},
                "progress": {"type": "integer", "description": "任务进度，系统会限制在0-100", "minimum": 0, "maximum": 100},
                "status": {
                    "type": "string",
                    "description": "任务状态",
                    "enum": ["pending_dispatch", "dispatched", "running", "completed", "failed", "cancelled"],
                },
                "run_id": {"type": "string", "description": "后续执行系统run_id"},
                "message": {"type": "string", "description": "进度事件说明"},
                "metadata": {"type": "object", "description": "进度事件元数据"},
            },
        }
    ),
    response=_response("更新成功", TASK_SCHEMA, {"400": "参数错误", "404": "任务不存在"}),
)
@requires_permission("command_center:u")
async def task_progress(request: Request):
    data = _require_json(request)
    task_id = data.get("id")
    if not task_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)
    operator = request.ctx.session["account_info"]
    task = await update_task_progress(task, data, operator.id)
    return response_ok(task.json)


@command_bp.post("/tasks/dispatch")
@openapi.definition(
    summary="重新触发任务调度",
    description="对已有任务重新执行调度；可传agent_id更新用户指定智能体，调度器仍会按能力、状态和负载校验并自动纠正。需要command_center:u权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "任务ID"},
                "agent_id": {"type": "string", "description": "可选，新的用户指定智能体ID或agent_key"},
            },
        }
    ),
    response=_response("调度完成", TASK_SCHEMA, {"400": "参数错误", "404": "任务不存在"}),
)
@requires_permission("command_center:u")
async def retry_dispatch(request: Request):
    data = _require_json(request)
    task_id = data.get("id")
    if not task_id:
        raise CustomError(ErrorCode.PARAM_VALUE_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)
    if "agent_id" in data:
        task.requested_agent_id = data.get("agent_id")
        await task.save(update_fields=["requested_agent_id"])
    operator = request.ctx.session["account_info"]
    task = await dispatch_task(task, operator.id)
    return response_ok(task.json)
