# coding=utf-8

from sanic import Blueprint, Request
from sanic_ext import openapi
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.large_screen import LargeScreen

screen_bp = Blueprint("large_screen", url_prefix="/large_screen")
__all__ = ["screen_bp"]


def _require_json(request: Request) -> dict:
    return request.json or {}


def _validate_screen_data(screen_data):
    if screen_data is None or not isinstance(screen_data, (dict, list)):
        raise CustomError(ErrorCode.PARAM_ERROR)


@screen_bp.post("/add")
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


@screen_bp.post("/list")
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


@screen_bp.post("/detail")
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
    large_screen = await LargeScreen.get_or_none(id=screen_id, tenant_id=tenant_id, deleted=False)
    if not large_screen:
        raise CustomError(ErrorCode.NOT_FOUND)

    return response_ok(large_screen.json)


@screen_bp.post("/update")
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
    large_screen = await LargeScreen.get_or_none(id=screen_id, tenant_id=tenant_id, deleted=False)
    if not large_screen:
        raise CustomError(ErrorCode.NOT_FOUND)

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


@screen_bp.post("/delete")
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
    if not screen_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    large_screen = await LargeScreen.get_or_none(id=screen_id, tenant_id=tenant_id, deleted=False)
    if not large_screen:
        raise CustomError(ErrorCode.NOT_FOUND)

    large_screen.deleted = True
    await large_screen.save(update_fields=["deleted", "utime"])
    return response_ok({"id": screen_id})
