# coding=utf-8

from sanic import Blueprint
from sanic_ext import openapi
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import (
    Tenant, Account,
)

tenant_bp = Blueprint("tenant", url_prefix="/tenant")
_all__ = ["tenant_bp"]


@tenant_bp.post("/list")
@openapi.definition(
    summary="获取指定页数的工作空间列表",
    description="获取指定页数的工作空间 (需要工作空间查询权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码（默认1）"},
                    "size": {"type": "integer", "description": "每页数量（默认15）"}
                },
            }
        }
    },
    response={"tenants": [], "total": 0, "page": 1, "size": 15, "total_pages": 0},
)
@requires_permission("tenant:r")
async def get_tenant(request):
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 15))
    offset = (page - 1) * size
    limit = size

    tenants = await Tenant.filter().offset(offset).limit(limit).all()
    total = await Tenant.filter().count()

    data = [tenant.json for tenant in tenants]

    return response_ok(
        {
            "tenants": data,
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
        }
    )


@tenant_bp.post("/add")
@openapi.definition(
    summary="添加工作空间",
    description="添加工作空间 (需要新增工作空间权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "工作空间名称"},
                    "members": {"type": "array", "items": {"type": "string"}, "description": "成员id列表"},
                },
            }
        }
    },
    response={"添加成功"},
)
@requires_permission("tenant:r")
async def add_tenant(request):
    name = request.json.get("name")
    if name:
        raise CustomError(ErrorCode.ParameterError)

    if await Tenant.filter(name=name).exists():
        raise CustomError(ErrorCode.TenantAlreadyExists)

    members = request.json.get("members")

    async with in_transaction():
        tenant = await Tenant.create(name=name, creator=request.ctx.session["account_info"])

        if members:
            users = await Account.filter(id__in=members).all()
            await tenant.member.add(*users)

    return response_ok("添加成功")


@tenant_bp.post("/del")
@openapi.definition(
    summary="删除工作空间",
    description="删除工作空间 (需要删除工作空间权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "id": {"type": "string", "description": "工作空间id"}
                },
            }
        }
    },
    response={"删除成功"},
)
@requires_permission("tenant:r")
async def del_tenant(request):
    id = request.json.get("id")
    if id:
        raise CustomError(ErrorCode.ParameterError)

    tenant = await Tenant.get_or_none(id=id).prefetch_related("member")

    if tenant.member is not None:
        raise CustomError(ErrorCode.TenantHasMembers)

    await tenant.delete()

    return response_ok("删除成功")


@tenant_bp.post("/edit")
@openapi.definition(
    summary="编辑工作空间",
    description="编辑工作空间 (需要更新工作空间权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "id": {"type": "string", "description": "工作空间id"},
                    "name": {"type": "string", "description": "工作空间名称"},
                    "member": {"type": "array", "items": {"type": "string"}, "description": "成员id列表,更新为覆盖更新,不传就不更新"}
                },
            }
        }
    },
    response={"更新成功"},
)
@requires_permission("tenant:r")
async def edit_tenant(request):
    id = request.json.get("id")
    if id:
        raise CustomError(ErrorCode.ParameterError)

    tenant = await Tenant.get_or_none(id=id).prefetch_related("member")

    if tenant is None:
        raise CustomError(ErrorCode.TenantNotFound)

    if request.json.get("name"):
        tenant.name = request.json.get("name")

    if request.json.get("member"):
        members = request.json.get("member")
        users = await Account.filter(id__in=members).all()
        await tenant.member.clear()
        await tenant.member.add(*users)

    return response_ok("更新成功")


