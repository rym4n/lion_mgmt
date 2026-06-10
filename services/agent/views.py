# coding=utf-8
import asyncio
import base64
import os
from datetime import datetime
from pathlib import Path

from sanic import Blueprint, Request
from sanic_ext import openapi
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.nanobot import NanobotClient
from libs.security.authorization import requires_permission
from libs.utils import gen_run_id
from models.command_center import CommandAgent, CommandTask, CommandTaskEvent
from models.report import ReportDocument
from settings import NANOBOT_WORKSPACE_BASE, REPORT_TEMPLATE_BASE, REPORT_USER_BASE, TZ

agent_bp = Blueprint("agent", url_prefix="/agent")


__all__ = ["agent_bp"]

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

EXECUTE_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string", "description": "执行追踪ID"},
        "task_id": {"type": "string", "description": "CommandTask ID"},
        "status": {
            "type": "string",
            "description": "执行状态",
            "enum": ["running", "completed", "failed"],
        },
        "result": {"type": "string", "nullable": True, "description": "执行结果内容"},
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

    # 内置 agent 不分租户，所有租户都能看到；非内置 agent 按租户隔离
    base_condition = Q(deleted=False) & (Q(tenant_id=tenant_id) | Q(is_builtin=True))
    extra = Q()
    if data.get("status"):
        extra &= Q(status=data["status"])
    if data.get("runtime"):
        extra &= Q(runtime=data["runtime"])
    if "is_builtin" in data:
        extra &= Q(is_builtin=bool(data.get("is_builtin")))
    if data.get("keyword"):
        keyword = data["keyword"]
        extra &= Q(agent_key__icontains=keyword) | Q(name__icontains=keyword) | Q(description__icontains=keyword)
    condition = base_condition & extra

    queryset = CommandAgent.filter(condition).distinct()
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


async def _run_agent_task(task_id: str, tenant_id: str, agent: CommandAgent, prompt: str, run_id: str, operator_id: str) -> None:
    """后台异步执行 Nanobot 任务，结果写回 CommandTask"""
    task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    if not task:
        return

    workspace = Path(NANOBOT_WORKSPACE_BASE) / tenant_id / agent.agent_key / run_id
    client = NanobotClient(session_id=run_id, workspace=workspace)

    try:
        result = await client.ask(message=prompt, streaming=False, session_key=run_id)
        task.status = "completed"
        task.progress = 100
        task.result = result
        task.finished_at = datetime.now(TZ)
        await task.save(update_fields=["status", "progress", "result", "finished_at", "utime"])
        await CommandTaskEvent.create(
            tenant_id=tenant_id,
            task_id=task_id,
            event_type="task_completed",
            status="completed",
            progress=100,
            agent_key=agent.agent_key,
            message="任务执行完成",
            metadata={"run_id": run_id},
            operator_id=operator_id,
        )
    except Exception as exc:
        task.status = "failed"
        task.finished_at = datetime.now(TZ)
        await task.save(update_fields=["status", "finished_at", "utime"])
        await CommandTaskEvent.create(
            tenant_id=tenant_id,
            task_id=task_id,
            event_type="task_failed",
            status="failed",
            agent_key=agent.agent_key,
            message=f"任务执行失败: {exc}",
            metadata={"run_id": run_id},
            operator_id=operator_id,
        )


@agent_bp.post("/execute")
@openapi.definition(
    summary="向智能体下发执行任务",
    description="指定智能体和任务内容，后台异步创建 Nanobot 实例执行任务，立即返回 run_id 和 task_id 用于后续查询结果。需要 agent:u 权限。",
    body=_json_body(
        {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "agent_id": {"type": "string", "description": "智能体 ID（与 agent_key 二选一）"},
                "agent_key": {"type": "string", "description": "智能体逻辑标识（与 agent_id 二选一）"},
                "prompt": {"type": "string", "description": "任务内容/提示词"},
                "title": {"type": "string", "description": "任务标题，不传则截取 prompt 前 50 字"},
                "room_id": {"type": "string", "description": "可选，关联作战室 ID"},
            },
        }
    ),
    response=_response(
        "任务已提交",
        EXECUTE_RESULT_SCHEMA,
        {"400": "参数错误", "404": "智能体不存在"},
    ),
)
@requires_permission("agent:u")
async def execute_agent(request: Request):
    data = _require_json(request)
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    agent = await _get_agent(tenant_id, data.get("agent_id"), data.get("agent_key"))
    if not agent:
        raise CustomError(ErrorCode.NOT_FOUND)

    operator_id = _session_user_id(request)
    title = str(data.get("title") or prompt[:50]).strip()
    run_id = gen_run_id()

    task = await CommandTask.create(
        tenant_id=tenant_id,
        title=title,
        source="agent_execute",
        status="running",
        progress=0,
        payload={"prompt": prompt},
        assigned_agent_id=agent.id,
        assigned_agent_key=agent.agent_key,
        dispatch_status="user_specified",
        run_id=run_id,
        room_id=data.get("room_id"),
        creator_id=operator_id,
        started_at=datetime.now(TZ),
    )
    await CommandTaskEvent.create(
        tenant_id=tenant_id,
        task_id=task.id,
        event_type="task_created",
        status="running",
        progress=0,
        agent_key=agent.agent_key,
        message="任务已创建并开始执行",
        metadata={"run_id": run_id},
        operator_id=operator_id,
    )

    asyncio.create_task(_run_agent_task(str(task.id), tenant_id, agent, prompt, run_id, operator_id or ""))

    return response_ok({"run_id": run_id, "task_id": str(task.id), "status": "running"})


@agent_bp.post("/execute/result")
@openapi.definition(
    summary="查询智能体任务执行结果",
    description="按 run_id 或 task_id 查询任务执行状态和结果。需要 agent:r 权限。",
    body=_json_body(
        {
            "type": "object",
            "oneOf": [{"required": ["run_id"]}, {"required": ["task_id"]}],
            "properties": {
                "run_id": {"type": "string", "description": "执行追踪ID"},
                "task_id": {"type": "string", "description": "任务ID"},
            },
        }
    ),
    response=_response(
        "查询成功",
        EXECUTE_RESULT_SCHEMA,
        {"400": "参数错误", "404": "任务不存在"},
    ),
)
@requires_permission("agent:r")
async def execute_result(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")

    run_id = data.get("run_id")
    task_id = data.get("task_id")
    if not run_id and not task_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if task_id:
        task = await CommandTask.get_or_none(id=task_id, tenant_id=tenant_id, deleted=False)
    else:
        task = await CommandTask.filter(run_id=run_id, tenant_id=tenant_id, deleted=False).first()

    if not task:
        raise CustomError(ErrorCode.NOT_FOUND)

    return response_ok({
        "run_id": task.run_id,
        "task_id": str(task.id),
        "status": task.status,
        "result": task.result,
    })


# ---------------------------------------------------------------------------
# 报告模板 & 文档接口
# ---------------------------------------------------------------------------

REPORT_TEMPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "模板标识（文件名不含扩展名）"},
        "name": {"type": "string", "description": "模板显示名称"},
        "filename": {"type": "string", "description": "文件名"},
    },
}

REPORT_DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "文档ID"},
        "name": {"type": "string", "description": "文件名"},
        "template_key": {"type": "string", "nullable": True, "description": "来源模板标识"},
        "ctime": {"type": "string", "description": "创建时间"},
        "utime": {"type": "string", "description": "更新时间"},
    },
}


def _template_key(filename: str) -> str:
    """去掉 .docx 后缀作为 key"""
    return Path(filename).stem


def _user_report_dir(tenant_id: str, user_id: str) -> Path:
    p = Path(REPORT_USER_BASE) / tenant_id / user_id
    p.mkdir(parents=True, exist_ok=True)
    return p


@agent_bp.post("/report/templates")
@openapi.definition(
    summary="获取内置报告模板列表",
    description="扫描 workspace-templates/reports/ 目录，返回所有 .docx 模板。需要 agent:r 权限。",
    response=_response("模板列表", {"type": "array", "items": REPORT_TEMPLATE_SCHEMA}),
)
@requires_permission("agent:r")
async def report_templates(request: Request):
    base = Path(REPORT_TEMPLATE_BASE)
    templates = []
    if base.exists():
        for f in sorted(base.iterdir()):
            if f.is_file() and f.suffix.lower() == ".docx":
                templates.append({
                    "key": _template_key(f.name),
                    "name": _template_key(f.name),
                    "filename": f.name,
                })
    return response_ok(templates)


@agent_bp.post("/report/template/content")
@openapi.definition(
    summary="获取内置报告模板内容（base64）",
    description="按 template_key 返回模板 .docx 文件的 base64 编码内容。需要 agent:r 权限。",
    body=_json_body({
        "type": "object",
        "required": ["template_key"],
        "properties": {
            "template_key": {"type": "string", "description": "模板标识"},
        },
    }),
    response=_response("模板内容", {
        "type": "object",
        "properties": {
            "template_key": {"type": "string"},
            "filename": {"type": "string"},
            "content": {"type": "string", "description": "base64 编码的 .docx 文件内容"},
        },
    }, {"404": "模板不存在"}),
)
@requires_permission("agent:r")
async def report_template_content(request: Request):
    data = _require_json(request)
    key = str(data.get("template_key") or "").strip()
    if not key:
        raise CustomError(ErrorCode.PARAM_ERROR)

    path = Path(REPORT_TEMPLATE_BASE) / f"{key}.docx"
    if not path.exists() or not path.is_file():
        raise CustomError(ErrorCode.NOT_FOUND)

    content = base64.b64encode(path.read_bytes()).decode("utf-8")
    return response_ok({"template_key": key, "filename": path.name, "content": content})


@agent_bp.post("/report/save")
@openapi.definition(
    summary="另存报告文档",
    description="将前端传来的 .docx base64 内容写入用户目录，创建 ReportDocument 记录。需要 agent:u 权限。",
    body=_json_body({
        "type": "object",
        "required": ["name", "content"],
        "properties": {
            "name": {"type": "string", "description": "文件名（不含 .docx 也可，自动补全）"},
            "template_key": {"type": "string", "nullable": True, "description": "来源模板标识"},
            "content": {"type": "string", "description": "base64 编码的 .docx 文件内容"},
            "doc_id": {"type": "string", "nullable": True, "description": "若传入则覆盖已有文档"},
        },
    }),
    response=_response("保存成功", REPORT_DOC_SCHEMA, {"400": "参数错误"}),
)
@requires_permission("agent:u")
async def report_save(request: Request):
    data = _require_json(request)
    name = str(data.get("name") or "").strip()
    content_b64 = str(data.get("content") or "").strip()
    if not name or not content_b64:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 解码内容
    try:
        file_bytes = base64.b64decode(content_b64)
    except Exception:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if not name.lower().endswith(".docx"):
        name = name + ".docx"

    tenant_id = request.ctx.session.get("tenant_id")
    user_id = str(_session_user_id(request) or "")
    template_key = data.get("template_key") or None
    doc_id = data.get("doc_id") or None

    user_dir = _user_report_dir(tenant_id, user_id)

    if doc_id:
        # 覆盖已有文档
        doc = await ReportDocument.get_or_none(id=doc_id, tenant_id=tenant_id, deleted=False)
        if not doc:
            raise CustomError(ErrorCode.NOT_FOUND)
        old_path = user_dir / doc.file_path
        if old_path.exists():
            old_path.unlink()
        rel_path = name
        (user_dir / rel_path).write_bytes(file_bytes)
        doc.name = name
        doc.template_key = template_key
        doc.file_path = rel_path
        await doc.save(update_fields=["name", "template_key", "file_path", "utime"])
    else:
        rel_path = name
        # 同名文件加序号避免冲突
        stem = Path(name).stem
        idx = 1
        while (user_dir / rel_path).exists():
            rel_path = f"{stem}_{idx}.docx"
            idx += 1
        (user_dir / rel_path).write_bytes(file_bytes)
        doc = await ReportDocument.create(
            tenant_id=tenant_id,
            owner_id=user_id if user_id else None,
            name=rel_path,
            template_key=template_key,
            file_path=rel_path,
        )

    return response_ok(doc.json)


@agent_bp.post("/report/list")
@openapi.definition(
    summary="获取当前用户的报告文档列表",
    description="返回当前登录用户保存的所有报告文档。需要 agent:r 权限。",
    response=_response("文档列表", {"type": "array", "items": REPORT_DOC_SCHEMA}),
)
@requires_permission("agent:r")
async def report_list(request: Request):
    tenant_id = request.ctx.session.get("tenant_id")
    user_id = str(_session_user_id(request) or "")
    docs = await ReportDocument.filter(
        tenant_id=tenant_id, owner_id=user_id, deleted=False
    ).order_by("-utime")
    return response_ok([d.json for d in docs])


@agent_bp.post("/report/content")
@openapi.definition(
    summary="获取用户报告文档内容（base64）",
    description="按 doc_id 返回用户已保存报告的 .docx base64 内容。需要 agent:r 权限。",
    body=_json_body({
        "type": "object",
        "required": ["doc_id"],
        "properties": {
            "doc_id": {"type": "string", "description": "文档ID"},
        },
    }),
    response=_response("文档内容", {
        "type": "object",
        "properties": {
            "doc_id": {"type": "string"},
            "name": {"type": "string"},
            "content": {"type": "string", "description": "base64 编码的 .docx 文件内容"},
        },
    }, {"404": "文档不存在"}),
)
@requires_permission("agent:r")
async def report_content(request: Request):
    data = _require_json(request)
    doc_id = str(data.get("doc_id") or "").strip()
    if not doc_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    user_id = str(_session_user_id(request) or "")
    doc = await ReportDocument.get_or_none(id=doc_id, tenant_id=tenant_id, owner_id=user_id, deleted=False)
    if not doc:
        raise CustomError(ErrorCode.NOT_FOUND)

    user_dir = _user_report_dir(tenant_id, user_id)
    file_path = user_dir / doc.file_path
    if not file_path.exists():
        raise CustomError(ErrorCode.NOT_FOUND)

    content = base64.b64encode(file_path.read_bytes()).decode("utf-8")
    return response_ok({"doc_id": str(doc.id), "name": doc.name, "content": content})


@agent_bp.post("/report/polish")
@openapi.definition(
    summary="使用汇报助手润色报告段落",
    description="同步调用汇报助手（report_assistant）对报告段落进行润色，直接返回润色结果。需要 agent:u 权限。",
    body=_json_body({
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "报告原文段落 + 批注内容的组合文本"},
        },
    }),
    response=_response("润色结果", {
        "type": "object",
        "properties": {
            "result": {"type": "string", "description": "润色后的段落内容"},
        },
    }, {"404": "汇报助手智能体未初始化", "500": "润色执行失败"}),
)
@requires_permission("agent:u")
async def report_polish(request: Request):
    data = _require_json(request)
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    agent = await _get_agent(tenant_id, agent_key="report_assistant")
    if not agent:
        raise CustomError(ErrorCode.NOT_FOUND)

    run_id = gen_run_id()
    workspace = Path(NANOBOT_WORKSPACE_BASE) / tenant_id / agent.agent_key / run_id
    client = NanobotClient(session_id=run_id, workspace=workspace)

    try:
        result = await client.ask(message=prompt, streaming=False, session_key=run_id)
    except Exception as exc:
        raise CustomError(ErrorCode.INTERNAL_ERROR)

    return response_ok({"result": result})


