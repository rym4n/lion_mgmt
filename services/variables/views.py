#!/usr/bin/env python
# coding=utf-8
import time

from aiocache import caches
from sanic import Blueprint
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.variables import Variables

var_bp = Blueprint("var", url_prefix="/var")
_all__ = ["var_bp"]


@var_bp.post("/new")
@openapi.definition(
    summary="新建全局变量",
    description="创建一个新的全局变量，需要var:c权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "value"],
                "properties": {
                    "name": {"type": "string", "description": "变量名（唯一）"},
                    "value": {
                        "type": "object",
                        "description": "变量值，JSON格式对象，不是字符串",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "创建成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "name": {"type": "string"},
                                    "value": {"type": "object"},
                                    "type": {"type": "string"},
                                    "updator": {"type": "object"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("var:c")
async def new_variable(request):
    name = request.json.get("name")
    value = request.json.get("value")
    if not name or not value:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    var = await Variables.create(
        name=request.json["name"],
        value=value,
        type="global",
        updator=request.ctx.session["account_info"],
        tenant_id=tenant_id,
    )
    await caches.get("redis_chat").set(f"global_{var.name}_{tenant_id}", value)
    return response_ok(var.json)


@var_bp.post("/list")
@openapi.definition(
    summary="获取所有全局变量信息",
    description="获取所有全局变量列表，需要var:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码"},
                    "size": {"type": "integer", "description": "每页数量, 最大100"},
                    "name": {
                        "type": "string",
                        "description": "可选参数，变量名（精准匹配）",
                    },
                    "value": {
                        "type": "string",
                        "description": "可选参数，变量值（对JSONField内的元素进行精准匹配）",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "total": {"type": "integer"},
                                    "data": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                                "name": {"type": "string"},
                                                "value": {"type": "object"},
                                                "type": {"type": "string"},
                                                "updator": {"type": "object"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("var:r")
async def all_variables(request):
    req_json = request.json or {}
    page = max(1, int(req_json.get("page", 1) or 1))
    size = max(1, min(int(req_json.get("size", 15) or 15), 100))
    offset = (page - 1) * size
    tenant_id = request.ctx.session.get("tenant_id")
    qs = Variables.filter(type="global", tenant_id=tenant_id)

    name = (req_json.get("name") or "").strip()
    if name:
        qs = qs.filter(name=name)

    value = req_json.get("value")
    if value is not None and value != "":
        qs = qs.filter(value__contains=[value])

    total = await qs.count()
    records = []
    if total > 0:
        records = await qs.offset(offset).limit(size).all().prefetch_related("updator")
    return response_ok({"total": total, "data": [v.json for v in records]})


@var_bp.post("/del")
@openapi.definition(
    summary="删除全局变量",
    description="删除指定的全局变量，需要var:d权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "description": "变量名"}},
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("var:d")
async def delelte_variable(request):
    tenant_id = request.ctx.session.get("tenant_id")
    var = await Variables.get_or_none(name=request.json.get("name"), type="global", tenant_id=tenant_id)
    if not var:
        raise CustomError(ErrorCode.NOT_FOUND)
    await caches.get("redis_chat").delete(f"global_{var.name}")
    await var.update_from_dict({"deleted": True})
    return response_ok("success")


@var_bp.post("/update")
@openapi.definition(
    summary="更新全局变量",
    description="更新指定的全局变量，需要var:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "value"],
                "properties": {
                    "name": {"type": "string", "description": "变量名"},
                    "value": {"type": "object", "description": "变量值，JSON格式对象"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "更新成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("var:u")
async def update_variable(request):
    name = request.json.get("name")
    value = request.json.get("value")
    if not name or not value:
        raise CustomError(ErrorCode.PARAM_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    var = await Variables.get(name=name, type="global", tenant_id=tenant_id)
    if not var:
        raise CustomError(ErrorCode.NOT_FOUND)

    var.value = value
    var.updator = request.ctx.session["account_info"]
    await var.save()
    await caches.get("redis_chat").set(f"global_{var.name}", value)
    return response_ok("success")
