# coding=utf-8

from sanic import Blueprint
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.functions import Count
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authentication import refresh_token
from libs.security.authorization import requires_permission
from libs.utils import get_expiration_date
from models import Account, Tenant
from settings import (
    AUTHENTICATION_SESSION_EXPIRATION,
    SESSION_HTTPONLY,
    SESSION_SAMESITE,
    SESSION_SECURE,
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
                    "size": {"type": "integer", "description": "每页数量（默认15）"},
                    "name": {"type": "string", "description": "工作空间名称"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {"application/json": {"example": {"tenants": [], "total": 0, "page": 1, "size": 15, "total_pages": 0}}},
        }
    },
)
@requires_permission("tenant:r")
async def get_tenant(request):
    page = max(1, int(request.json.get("page", 1)))
    size = max(1, min(int(request.json.get("size", 15)), 100))
    name = request.json.get("name")
    offset = (page - 1) * size

    query = Tenant.filter()
    if name:
        query = query.filter(name__contains=name)

    tenants = await query.offset(offset).limit(size).all().prefetch_related("creator", "member")
    total = await query.count()

    data = []
    for tenant in tenants:
        data.append(tenant.json)
        data[-1]["member"] = len(tenant.member)

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
    response={"200": {"description": "添加成功"}},
)
@requires_permission("tenant:c")
async def add_tenant(request):
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if await Tenant.filter(name=name).exists():
        raise CustomError(ErrorCode.TENANT_ALREADY_EXISTS)

    members = request.json.get("members")

    async with in_transaction():
        tenant = await Tenant.create(name=name, creator=request.ctx.session["account_info"])

        if members:
            users = await Account.filter(id__in=members).all()
            await tenant.member.add(*users)
        await tenant.member.add(request.ctx.session["account_info"])

    return response_ok(tenant.json)


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
    response={"200": {"description": "删除成功"}},
)
@requires_permission("tenant:d")
async def del_tenant(request):
    tenant_id = request.json.get("id")
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if tenant_id == "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18":
        raise CustomError(ErrorCode.CANNOT_DELETE_TENANT)

    tenant = await Tenant.get_or_none(id=tenant_id).prefetch_related("member")
    if tenant is None:
        raise CustomError(ErrorCode.TENANT_NOT_FOUND)

    if tenant.member:
        if len(tenant.member) > 1:
            raise CustomError(ErrorCode.TENANT_HAS_MEMBERS)
        if str(tenant.member[0].id) != "43782755-6382-4f42-82a2-5e557370d858":
            raise CustomError(ErrorCode.TENANT_HAS_MEMBERS)

    tenant.deleted = True
    await tenant.save()

    return response_ok("success")


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
                    "add_members": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "新增成员id列表",
                    },
                    "del_members": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "删除成员id列表",
                    },
                },
            }
        }
    },
    response={"200": {"description": "更新成功"}},
)
@requires_permission("tenant:u")
async def edit_tenant(request):
    req = request.json or {}
    tenant_id = req.get("id")
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    name = (req.get("name") or "").strip()
    add_member_ids = set(req.get("add_members") or [])
    del_member_ids = set(req.get("del_members") or [])

    add_member_ids -= del_member_ids
    unsafe_usernames = []

    async with in_transaction():
        tenant = await Tenant.get_or_none(id=tenant_id)
        if tenant is None:
            raise CustomError(ErrorCode.TENANT_NOT_FOUND)

        if name:
            if await Tenant.filter(name=name).exists():
                raise CustomError(ErrorCode.TENANT_ALREADY_EXISTS)
            tenant.name = name
            await tenant.save(update_fields=["name"])

        if add_member_ids:
            existing_ids = set(await tenant.member.filter(id__in=add_member_ids).values_list("id", flat=True))
            ids_to_add = add_member_ids - existing_ids
            if ids_to_add:
                add_users = await Account.filter(id__in=ids_to_add)
                await tenant.member.add(*add_users)

        if del_member_ids:
            current_ids = set(await tenant.member.filter(id__in=del_member_ids).values_list("id", flat=True))
            # 筛选存在这个工作空间下的用户id
            ids_to_remove = current_ids
            if ids_to_remove:
                unsafe_users = await Account.filter(id__in=ids_to_remove).annotate(tenant_count=Count("member")).filter(tenant_count__lte=1)
                unsafe_ids = {u.id for u in unsafe_users}
                unsafe_usernames = [{"display_name": u.display_name, "username": u.username} for u in unsafe_users]
                # 剔除只有一个工作空间的用户
                ids_to_remove -= unsafe_ids
            if ids_to_remove:
                remove_users = await Account.filter(id__in=ids_to_remove)
                await tenant.member.remove(*remove_users)
                for user in remove_users:
                    if user.tenant_id == tenant.id:
                        new_tenant_id = await Tenant.filter(member=user).exclude(id=tenant.id).values_list("id", flat=True)
                        user.tenant_id = new_tenant_id[0] if new_tenant_id else None
                        await user.save(update_fields=["tenant_id"])

    if unsafe_usernames:
        raise CustomError(ErrorCode.DELETE_SONG_USERS_FAILED)

    return response_ok({"message": "更新成功"})


@tenant_bp.post("/switch")
@openapi.definition(
    summary="切换工作空间",
    description="切换工作空间",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "工作空间id"}},
            }
        }
    },
    response={"200": {"description": "切换成功"}},
)
@requires_permission("tenant:r")
async def switch_tenant(request):
    tenant_id = request.json.get("id")
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant = await Tenant.get_or_none(id=tenant_id).prefetch_related("member")

    if tenant is None:
        raise CustomError(ErrorCode.TENANT_NOT_FOUND)

    request.ctx.session["tenant_id"] = tenant.id
    new_token = refresh_token(request.ctx.session)

    resp_ok = response_ok({"tenant_id": tenant.id})
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
    response={
        "200": {
            "description": "Success",
            "content": {"application/json": {"example": {"tenants": []}}},
        }
    },
)
async def get_account_tenant(request):
    account = request.ctx.session["account_info"]

    tenants = await Tenant.filter(member__id=account.id, deleted=False).all()

    if tenants is None:
        return response_ok({"tenants": []})

    return response_ok({"tenants": [{"id": tenant.id, "name": tenant.name} for tenant in tenants]})


@tenant_bp.post("/user-list")
@openapi.definition(
    summary="获取全部用户信息",
    description="获取全部用户信息",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "分页的页码，默认1"},
                    "size": {"type": "integer", "description": "分页的大小，默认10"},
                    "username": {"type": "string", "description": "用户名，搜索时使用"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {"application/json": {"example": {"tenants": []}}},
        }
    },
)
@requires_permission("tenant:c")
async def get_all_user_info(request):
    req_json = request.json or {}
    page = max(1, int(req_json.get("page", 1)))
    size = max(1, min(int(req_json.get("size", 15)), 100))
    offset = (page - 1) * size

    condition = {"deleted": False, "type": "user"}
    if req_json.get("username"):
        condition["username__contains"] = req_json.get("username")
    total = await Account.filter(**condition).count()
    users = await Account.filter(**condition).offset(offset).limit(size).all().prefetch_related("roles", "member")
    data = []
    for u in users:
        tenants = await u.member.all()
        tenant_ids = [t.id for t in tenants]
        data.append({"id": u.id, "username": u.username, "display_name": u.display_name, "tenant_ids": tenant_ids})

    return response_ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
            "data": data,
        }
    )


@tenant_bp.post("/get-tenant-user")
@openapi.definition(
    summary="获取空间所有用户",
    description="获取空间所有用户",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "分页的页码，默认1"},
                    "size": {"type": "integer", "description": "分页的大小，默认10"},
                    "tenant_id": {"type": "string", "description": "工作空间id"},
                    "username": {"type": "string", "description": "用户名，搜索时使用"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "Success",
            "content": {"application/json": {"example": {"tenants": []}}},
        }
    },
)
@requires_permission("tenant:r")
async def get_tenant_user(request):
    req_json = request.json or {}
    page = max(1, int(req_json.get("page", 1)))
    size = max(1, min(int(req_json.get("size", 10)), 100))
    tenant_id = req_json.get("tenant_id")
    username = (req_json.get("username") or "").strip()
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if page < 1:
        page = 1
    if size < 1:
        size = 10
    offset = (page - 1) * size

    qs = Account.filter(
        deleted=False,
        type="user",
        member__id=tenant_id,
    )
    if username:
        qs = qs.filter(Q(username__contains=username) | Q(display_name__contains=username))
    total = await qs.count()
    rows = await qs.offset(offset).limit(size).values("id", "username", "display_name")

    return response_ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
            "data": rows,
        }
    )
