# coding=utf-8

from sanic import Request
from sanic_ext import openapi
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.large_screen import LargeScreen
from services.large_screen.action_executor import execute_action, extract_config_actions, get_action_value
from services.dashboard.views import dashboard_bp

__all__ = []


def _require_json(request: Request) -> dict:
    return request.json or {}


def _validate_screen_data(screen_data):
    if screen_data is None or not isinstance(screen_data, (dict, list)):
        raise CustomError(ErrorCode.PARAM_ERROR)


def _validate_action_config(action_config):
    if action_config is None or not isinstance(action_config, (dict, list)):
        raise CustomError(ErrorCode.PARAM_ERROR)


async def _get_large_screen(screen_id, tenant_id):
    if not screen_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    large_screen = await LargeScreen.get_or_none(id=screen_id, tenant_id=tenant_id, deleted=False)
    if not large_screen:
        raise CustomError(ErrorCode.NOT_FOUND)

    return large_screen


@dashboard_bp.post("/large_screen/add")
@openapi.definition(
    summary="新增大屏",
    description="新增一个大屏配置，需要large_screen:c权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "screen_data"],
                "properties": {
                    "name": {"type": "string", "description": "大屏名称"},
                    "screen_data": {
                        "type": "object",
                        "description": "大屏数据，JSON对象或数组",
                    },
                },
            }
        }
    },
)
@requires_permission("large_screen:c")
async def add_large_screen(request: Request):
    data = _require_json(request)
    name = (data.get("name") or "").strip()
    screen_data = data.get("screen_data")
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)
    _validate_screen_data(screen_data)

    tenant_id = request.ctx.session.get("tenant_id")
    if await LargeScreen.filter(name=name, tenant_id=tenant_id, deleted=False).exists():
        raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)

    large_screen = await LargeScreen.create(name=name, screen_data=screen_data, tenant_id=tenant_id)
    return response_ok(large_screen.json)


@dashboard_bp.post("/large_screen/list")
@openapi.definition(
    summary="查询大屏列表",
    description="分页查询大屏配置，需要large_screen:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码，默认1"},
                    "size": {"type": "integer", "description": "每页数量，默认15，最大100"},
                    "name": {"type": "string", "description": "大屏名称，模糊匹配"},
                },
            }
        }
    },
)
@requires_permission("large_screen:r")
async def list_large_screens(request: Request):
    data = _require_json(request)
    page = max(1, int(data.get("page", 1) or 1))
    size = max(1, min(int(data.get("size", 15) or 15), 100))
    tenant_id = request.ctx.session.get("tenant_id")

    condition = Q(tenant_id=tenant_id, deleted=False)
    name = (data.get("name") or "").strip()
    if name:
        condition = condition & Q(name__icontains=name)

    total = await LargeScreen.filter(condition).count()
    records = []
    if total > 0:
        records = await LargeScreen.filter(condition).offset((page - 1) * size).limit(size).all()

    return response_ok({"total": total, "page": page, "size": size, "data": [item.json for item in records]})


@dashboard_bp.post("/large_screen/detail")
@openapi.definition(
    summary="查询大屏详情",
    description="查询指定大屏配置，需要large_screen:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "大屏ID"}},
            }
        }
    },
)
@requires_permission("large_screen:r")
async def large_screen_detail(request: Request):
    data = _require_json(request)
    screen_id = data.get("id")
    if not screen_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    large_screen = await _get_large_screen(screen_id, tenant_id)

    return response_ok(large_screen.json)


@dashboard_bp.post("/large_screen/update")
@openapi.definition(
    summary="更新大屏",
    description="更新指定大屏配置，需要large_screen:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "大屏ID"},
                    "name": {"type": "string", "description": "大屏名称"},
                    "screen_data": {
                        "type": "object",
                        "description": "大屏数据，JSON对象或数组",
                    },
                },
            }
        }
    },
)
@requires_permission("large_screen:u")
async def update_large_screen(request: Request):
    data = _require_json(request)
    screen_id = data.get("id")
    if not screen_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    large_screen = await _get_large_screen(screen_id, tenant_id)

    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise CustomError(ErrorCode.PARAM_ERROR)
        name_exists = await LargeScreen.filter(name=name, tenant_id=tenant_id, deleted=False).exists()
        if name != large_screen.name and name_exists:
            raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
        large_screen.name = name

    if "screen_data" in data:
        _validate_screen_data(data.get("screen_data"))
        large_screen.screen_data = data["screen_data"]

    if "name" not in data and "screen_data" not in data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    await large_screen.save()
    return response_ok(large_screen.json)


@dashboard_bp.post("/large_screen/delete")
@openapi.definition(
    summary="删除大屏",
    description="删除指定大屏配置，需要large_screen:d权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "大屏ID"}},
            }
        }
    },
)
@requires_permission("large_screen:d")
async def delete_large_screen(request: Request):
    data = _require_json(request)
    screen_id = data.get("id")
    tenant_id = request.ctx.session.get("tenant_id")
    large_screen = await _get_large_screen(screen_id, tenant_id)

    large_screen.deleted = True
    await large_screen.save(update_fields=["deleted", "utime"])
    return response_ok({"id": screen_id})


@dashboard_bp.post("/large_screen/action_config/add")
@openapi.definition(
    summary="新增大屏动作配置",
    description="为指定大屏新增动作配置，需要large_screen:c权限；动作配置结构可参考巡检自定义报告配置",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id", "action_config"],
                "properties": {
                    "id": {"type": "string", "description": "大屏ID"},
                    "action_config": {"type": "object", "description": "大屏动作配置，JSON对象或数组"},
                },
            }
        }
    },
)
@requires_permission("large_screen:c")
async def add_large_screen_action_config(request: Request):
    data = _require_json(request)
    action_config = data.get("action_config")
    _validate_action_config(action_config)

    tenant_id = request.ctx.session.get("tenant_id")
    large_screen = await _get_large_screen(data.get("id"), tenant_id)
    if large_screen.action_config:
        raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)

    large_screen.action_config = action_config
    await large_screen.save(update_fields=["action_config", "utime"])
    return response_ok({"id": large_screen.id, "action_config": large_screen.action_config})


@dashboard_bp.post("/large_screen/action_config/detail")
@openapi.definition(
    summary="查询大屏动作配置",
    description="查询指定大屏的动作配置，需要large_screen:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "大屏ID"}},
            }
        }
    },
)
@requires_permission("large_screen:r")
async def large_screen_action_config_detail(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    large_screen = await _get_large_screen(data.get("id"), tenant_id)
    return response_ok({"id": large_screen.id, "action_config": large_screen.action_config})


@dashboard_bp.post("/large_screen/action_config/update")
@openapi.definition(
    summary="更新大屏动作配置",
    description="覆盖更新指定大屏动作配置，需要large_screen:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id", "action_config"],
                "properties": {
                    "id": {"type": "string", "description": "大屏ID"},
                    "action_config": {"type": "object", "description": "大屏动作配置，JSON对象或数组"},
                },
            }
        }
    },
)
@requires_permission("large_screen:u")
async def update_large_screen_action_config(request: Request):
    data = _require_json(request)
    action_config = data.get("action_config")
    _validate_action_config(action_config)

    tenant_id = request.ctx.session.get("tenant_id")
    large_screen = await _get_large_screen(data.get("id"), tenant_id)
    large_screen.action_config = action_config
    await large_screen.save(update_fields=["action_config", "utime"])
    return response_ok({"id": large_screen.id, "action_config": large_screen.action_config})


@dashboard_bp.post("/large_screen/action_config/delete")
@openapi.definition(
    summary="删除大屏动作配置",
    description="清空指定大屏动作配置，需要large_screen:d权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "大屏ID"}},
            }
        }
    },
)
@requires_permission("large_screen:d")
async def delete_large_screen_action_config(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    large_screen = await _get_large_screen(data.get("id"), tenant_id)
    large_screen.action_config = None
    await large_screen.save(update_fields=["action_config", "utime"])
    return response_ok({"id": large_screen.id, "action_config": large_screen.action_config})


@dashboard_bp.post("/large_screen/action/execute")
@openapi.definition(
    summary="执行大屏动作",
    description="执行指定大屏已配置的动作并返回执行结果；不传action_index时执行全部动作，需要large_screen:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "大屏ID"},
                    "action_index": {"type": "integer", "description": "动作下标，不传则执行全部动作"},
                    "action_input": {"type": "object", "description": "本次执行临时覆盖动作入参"},
                },
            }
        }
    },
)
@requires_permission("large_screen:u")
async def execute_large_screen_action(request: Request):
    data = _require_json(request)
    tenant_id = request.ctx.session.get("tenant_id")
    large_screen = await _get_large_screen(data.get("id"), tenant_id)

    actions = extract_config_actions(large_screen.action_config)
    if not actions:
        raise CustomError(ErrorCode.PARAM_ERROR)

    override_action_input = data.get("action_input") if "action_input" in data else None
    if "action_index" in data:
        try:
            action_index = int(data.get("action_index"))
            if action_index < 0:
                raise ValueError
            selected_actions = [(action_index, actions[action_index])]
        except (TypeError, ValueError, IndexError):
            raise CustomError(ErrorCode.PARAM_ERROR)
    else:
        selected_actions = list(enumerate(actions))

    results = []
    for action_index, action in selected_actions:
        result = await execute_action(action, request, tenant_id, override_action_input)
        result["action_index"] = action_index
        result["action_name"] = get_action_value(action, "action_name", "name")
        results.append(result)

    return response_ok({"id": large_screen.id, "results": results})
