# coding=utf-8
from sanic import Blueprint
from sanic_ext import openapi
from tortoise.functions import Count
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authentication import refresh_token
from libs.security.authorization import requires_permission
from libs.utils import get_expiration_date
from models import (
    Tenant,
    Account,
)
from settings import AUTHENTICATION_SESSION_EXPIRATION, SESSION_SECURE, SESSION_SAMESITE, SESSION_HTTPONLY

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
                    "size": {"type": "integer", "description": "每页数量（默认15）"},
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

    tenants = await Tenant.filter().offset(offset).limit(limit).all().prefetch_related("creator", "member")
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
                "required": ["name", "members"],
                "properties": {
                    "name": {"type": "string", "description": "工作空间名称"},
                    "members": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "成员id列表",
                    },
                },
            }
        }
    },
    response={"添加成功"},
)
@requires_permission("tenant:c")
async def add_tenant(request):
    name = request.json.get("name")
    if not name:
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
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "工作空间id"}},
            }
        }
    },
    response={"删除成功"},
)
@requires_permission("tenant:d")
async def del_tenant(request):
    id = request.json.get("id")
    if not id:
        raise CustomError(ErrorCode.ParameterError)

    if id == "0":
        raise CustomError(ErrorCode.CannotDeleteTenant)

    tenant = await Tenant.get_or_none(id=id).prefetch_related("member")

    if tenant.member:
        if len(tenant.member) > 1:
            raise CustomError(ErrorCode.TenantHasMembers)
        if str(tenant.member[0].id) != "43782755-6382-4f42-82a2-5e557370d858":
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
                "required": ["id", "members"],
                "properties": {
                    "id": {"type": "string", "description": "工作空间id"},
                    "name": {"type": "string", "description": "工作空间名称"},
                    "members": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "成员id列表,更新为覆盖更新,不传就不更新",
                    },
                },
            }
        }
    },
    response={"更新成功"},
)
@requires_permission("tenant:u")
async def edit_tenant(request):
    tenant_id = request.json.get("id")
    if not tenant_id:
        raise CustomError(ErrorCode.ParameterError)

    async with in_transaction():
        tenant = await Tenant.get_or_none(id=tenant_id).prefetch_related("member")
        if tenant is None:
            raise CustomError(ErrorCode.TenantNotFound)

        name = request.json.get("name")
        if name:
            tenant.name = name
            await tenant.save(update_fields=["name"])

        unsafe_usernames = []

        if "members" in request.json:
            target_member_ids = set(request.json.get("members") or [])

            current_members = await tenant.member.all()
            current_member_ids = {u.id for u in current_members}

            ids_to_add = target_member_ids - current_member_ids
            ids_to_remove = current_member_ids - target_member_ids

            if ids_to_remove:
                unsafe_users = await Account.filter(
                    id__in=ids_to_remove
                ).annotate(
                    tenant_count=Count("member")
                ).filter(
                    tenant_count__lte=1
                )

                unsafe_ids = {u.id for u in unsafe_users}
                unsafe_usernames = [u.display_name for u in unsafe_users]

                ids_to_remove -= unsafe_ids

            if ids_to_remove:
                remove_users = await Account.filter(id__in=ids_to_remove)
                await tenant.member.remove(*remove_users)
                for user in remove_users:
                    if user.tenant_id == tenant.id:
                        remaining_tenants = await user.member.all()
                        if remaining_tenants:
                            tenant_new_id = remaining_tenants[0].id
                            user.tenant_id = tenant_new_id
                        else:
                            user.tenant_id = None
                        await user.save(update_fields=["tenant_id"])

            if ids_to_add:
                add_users = await Account.filter(id__in=ids_to_add)
                await tenant.member.add(*add_users)

    if unsafe_usernames:
        return response_ok(f"更新成功，但以下用户未被移除（仅属于一个工作空间）: {', '.join(unsafe_usernames)}")

    return response_ok("更新成功")


@tenant_bp.post("/switch")
@openapi.definition(
    summary="切换工作空间",
    description="切换工作空间",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "工作空间id"}
                },
            }
        }
    },
    response={"切换成功"},
)
@requires_permission("tenant:r")
async def switch_tenant(request):
    id = request.json.get("id")
    if not id:
        raise CustomError(ErrorCode.ParameterError)

    tenant = await Tenant.get_or_none(id=id).prefetch_related("member")

    if tenant is None:
        raise CustomError(ErrorCode.TenantNotFound)

    request.ctx.session["tenant_id"] = tenant.id
    new_token = refresh_token(request.ctx.session)

    resp_ok = response_ok("切换成功")
    resp_ok.cookies.add_cookie(
        "aioe-auth",
        new_token,
        httponly=SESSION_HTTPONLY,
        samesite=SESSION_SAMESITE,
        secure=SESSION_SECURE,
        expires=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
    )

    return resp_ok


@tenant_bp.post("/get")
@openapi.definition(
    summary="获取当前用户所有的工作空间",
    description="获取当前用户所有的工作空间",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {},
            }
        }
    },
    response={"tenants": []},
)
@requires_permission("tenant:r")
async def get_account_tenant(request):
    account = request.ctx.session["account_info"]

    tenants = await Tenant.filter(member__id=account.id, deleted=False).all()

    if tenants is None:
        return response_ok({"tenants": []})

    return response_ok({"tenants": [{"id": tenant.id, "name": tenant.name} for tenant in tenants]})
