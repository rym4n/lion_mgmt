# coding=utf-8
import logging

from sanic import Blueprint
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.functions import Count
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.maintenance import (
    DEFAULT_TENANT_ID,
    can_switch_tenant,
    should_clear_maintenance_mode,
)
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authentication import refresh_token
from libs.security.authorization import (
    TENANT_ADMIN_ROLE_NAME,
    is_superadmin,
    requires_permission,
)
from libs.utils import get_expiration_date
from models import Account, Role, Scene, Tenant
from services.openviking import (
    deprovision_tenant_knowledge,
    provision_tenant_knowledge,
    restore_tenant_knowledge,
    rotate_tenant_knowledge_key,
)
from settings import (
    AUTHENTICATION_SESSION_EXPIRATION,
    SESSION_HTTPONLY,
    SESSION_SAMESITE,
    SESSION_SECURE,
)

tenant_bp = Blueprint("tenant", url_prefix="/tenant")
__all__ = ["tenant_bp"]
logger = logging.getLogger(__name__)


def _normalize_tenant_name(value):
    return value.strip() if isinstance(value, str) else ""


def _validate_tenant_member_removal(
    *, creator_id, ids_to_remove, admin_member_ids
):
    if creator_id in ids_to_remove:
        raise CustomError(ErrorCode.TENANT_CREATOR_MUST_REMAIN_ADMIN)
    if admin_member_ids and not (admin_member_ids - ids_to_remove):
        raise CustomError(ErrorCode.TENANT_MUST_HAVE_ADMIN)


def _tenant_admin_recovery_target_ids(*, creator_id, operator_id, member_ids):
    target_ids = {creator_id} if creator_id else set()
    if operator_id in member_ids:
        target_ids.add(operator_id)
    return target_ids


def _can_restore_tenant_admin(operator, current_tenant_id, target_tenant_id):
    return bool(
        operator
        and operator.username == "admin"
        and current_tenant_id == DEFAULT_TENANT_ID
        and target_tenant_id != DEFAULT_TENANT_ID
        and is_superadmin(operator)
    )


async def _provision_tenant_knowledge_best_effort(tenant_id):
    try:
        await provision_tenant_knowledge(tenant_id)
    except Exception:
        logger.warning(
            "Failed to provision knowledge for tenant %s",
            tenant_id,
            exc_info=True,
        )


async def _clear_maintenance_mode_for_non_default_tenant(request, tenant_id):
    if not should_clear_maintenance_mode(tenant_id):
        return
    await request.app.ctx.cache_client.set(
        request.ctx.session["id"],
        False,
        AUTHENTICATION_SESSION_EXPIRATION,
    )


@tenant_bp.post("/list")
@openapi.definition(
    summary="获取指定页数的租户列表",
    description="获取指定页数的租户 (需要租户查询权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码（默认1）"},
                    "size": {"type": "integer", "description": "每页数量（默认15）"},
                    "name": {"type": "string", "description": "租户名称"},
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

    query = Tenant.filter(deleted=False)
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
    summary="添加租户",
    description="添加租户 (需要新增租户权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "租户名称"},
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
    req = request.json or {}
    name = _normalize_tenant_name(req.get("name"))
    if not name:
        raise CustomError(ErrorCode.TENANT_NAME_INVALID)
    tenant = await Tenant.get_or_none(name=name, deleted=False).prefetch_related("creator")
    if tenant:
        raise CustomError(ErrorCode.TENANT_ALREADY_EXISTS)

    members = req.get("members")
    creator: Account = request.ctx.session["account_info"]

    async with in_transaction():
        tenant = await Tenant.create(name=name, creator=creator)

        if members:
            users = await Account.filter(id__in=members).all()
            await tenant.member.add(*users)
        await tenant.member.add(creator)

        role = await Role.create(
            name=TENANT_ADMIN_ROLE_NAME,
            description=TENANT_ADMIN_ROLE_NAME,
            # 租户管理员的权限随当前软件许可动态展开，避免新增菜单后继续
            # 依赖一份会过期的静态权限清单。许可证过滤仍由菜单接口负责。
            permissions=["*:*"],
            tenant_id=tenant.id,
        )
        await creator.roles.add(role)

        # 创建默认场景"日常运营"
        await Scene.create(name="日常运营", remark="默认场景", status=1, creator=creator, updator=creator, tenant_id=tenant.id)

    # 外部知识库配置是提交后的附加能力，失败不改变已成功创建的租户。
    await _provision_tenant_knowledge_best_effort(tenant.id)

    return response_ok(tenant.json)


@tenant_bp.post("/del")
@openapi.definition(
    summary="删除租户",
    description="删除租户 (需要删除租户权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "租户id"}},
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
    account = await Account.get_or_none(id="43782755-6382-4f42-82a2-5e557370d858")
    await tenant.member.remove(account)
    tenant.deleted = True
    await tenant.save()

    # 租户软删除时处理知识库凭证（阶段三）
    try:
        await deprovision_tenant_knowledge(tenant.id, soft_delete=True)
    except Exception as e:
        # 知识库软删除失败不阻塞租户删除，记录日志
        request.app.ctx.logger.warning(f"Failed to soft-delete knowledge for tenant {tenant.id}: {e}")

    return response_ok("success")


@tenant_bp.post("/edit")
@openapi.definition(
    summary="编辑租户",
    description="编辑租户 (需要更新租户权限)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id", "members"],
                "properties": {
                    "id": {"type": "string", "description": "租户id"},
                    "name": {"type": "string", "description": "租户名称"},
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

    name = None
    if "name" in req:
        name = _normalize_tenant_name(req.get("name"))
        if not name:
            raise CustomError(ErrorCode.TENANT_NAME_INVALID)
    add_member_ids = set(req.get("add_members") or [])
    del_member_ids = set(req.get("del_members") or [])

    add_member_ids -= del_member_ids
    unsafe_usernames = []

    async with in_transaction() as connection:
        tenant = await (
            Tenant.filter(id=tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if tenant is None:
            raise CustomError(ErrorCode.TENANT_NOT_FOUND)

        if name is not None:
            if (
                await Tenant.filter(name=name, deleted=False)
                .using_db(connection)
                .exclude(id=tenant_id)
                .exists()
            ):
                raise CustomError(ErrorCode.TENANT_ALREADY_EXISTS)
            if tenant.name != name:
                tenant.name = name
                await tenant.save(update_fields=["name"], using_db=connection)

        if add_member_ids:
            existing_ids = set(
                await tenant.member.filter(id__in=add_member_ids)
                .using_db(connection)
                .values_list("id", flat=True)
            )
            ids_to_add = add_member_ids - existing_ids
            if ids_to_add:
                add_users = await Account.filter(id__in=ids_to_add).using_db(
                    connection
                )
                await tenant.member.add(*add_users, using_db=connection)

        if del_member_ids:
            current_ids = set(
                await tenant.member.filter(id__in=del_member_ids)
                .using_db(connection)
                .values_list("id", flat=True)
            )
            # 筛选存在这个租户下的用户id
            ids_to_remove = current_ids
            if ids_to_remove:
                tenant_admin = await (
                    Role.filter(
                        name=TENANT_ADMIN_ROLE_NAME,
                        tenant_id=tenant.id,
                        deleted=False,
                    )
                    .using_db(connection)
                    .select_for_update()
                    .first()
                )
                admin_member_ids = set()
                if tenant_admin:
                    admin_member_ids = set(
                        await Account.filter(
                            deleted=False,
                            member__id=tenant.id,
                            roles__id=tenant_admin.id,
                        )
                        .using_db(connection)
                        .order_by("id")
                        .distinct()
                        .values_list("id", flat=True)
                    )
                _validate_tenant_member_removal(
                    creator_id=tenant.creator_id,
                    ids_to_remove=ids_to_remove,
                    admin_member_ids=admin_member_ids,
                )

                unsafe_users = (
                    await Account.filter(id__in=ids_to_remove)
                    .using_db(connection)
                    .annotate(tenant_count=Count("member"))
                    .filter(tenant_count__lte=1)
                )
                unsafe_ids = {u.id for u in unsafe_users}
                unsafe_usernames = [
                    {"display_name": u.display_name, "username": u.username}
                    for u in unsafe_users
                ]
                # 剔除只有一个租户的用户
                ids_to_remove -= unsafe_ids
            if ids_to_remove:
                remove_users = await Account.filter(id__in=ids_to_remove).using_db(
                    connection
                )
                await tenant.member.remove(*remove_users, using_db=connection)
                for user in remove_users:
                    if user.tenant_id == tenant.id:
                        new_tenant_id = (
                            await Tenant.filter(member=user)
                            .using_db(connection)
                            .exclude(id=tenant.id)
                            .values_list("id", flat=True)
                        )
                        user.tenant_id = new_tenant_id[0] if new_tenant_id else None
                        await user.save(
                            update_fields=["tenant_id"], using_db=connection
                        )

    if unsafe_usernames:
        raise CustomError(ErrorCode.DELETE_SONG_USERS_FAILED)

    return response_ok({"message": "更新成功"})


@tenant_bp.post("/restore-admin")
@openapi.definition(
    summary="恢复租户管理员",
    description="由默认租户的内置平台管理员恢复目标租户的管理员角色",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["tenant_id"],
                "properties": {
                    "tenant_id": {"type": "string", "description": "目标租户id"},
                },
            }
        }
    },
    response={"200": {"description": "恢复成功"}},
)
@requires_permission("tenant:u")
async def restore_tenant_admin(request):
    """恢复创建者，并在内置 admin 已是成员时为其补齐管理员角色。"""
    operator: Account = request.ctx.session["account_info"]
    current_tenant_id = request.ctx.session.get("tenant_id")
    tenant_id = (request.json or {}).get("tenant_id")
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if not _can_restore_tenant_admin(operator, current_tenant_id, tenant_id):
        raise CustomError(ErrorCode.TENANT_ADMIN_RECOVERY_FORBIDDEN)

    async with in_transaction() as connection:
        tenant = await (
            Tenant.filter(id=tenant_id, deleted=False)
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if tenant is None:
            raise CustomError(ErrorCode.TENANT_NOT_FOUND)

        role = await (
            Role.filter(
                name=TENANT_ADMIN_ROLE_NAME,
                tenant_id=tenant.id,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if role is None:
            role = await Role.create(
                name=TENANT_ADMIN_ROLE_NAME,
                description=TENANT_ADMIN_ROLE_NAME,
                permissions=["*:*"],
                tenant_id=tenant.id,
                using_db=connection,
            )

        member_ids = set(
            await tenant.member.filter()
            .using_db(connection)
            .values_list("id", flat=True)
        )
        target_ids = _tenant_admin_recovery_target_ids(
            creator_id=tenant.creator_id,
            operator_id=operator.id,
            member_ids=member_ids,
        )
        targets = (
            await Account.filter(id__in=target_ids, deleted=False)
            .using_db(connection)
            .select_for_update()
            .all()
        )
        restored = []
        for target in targets:
            is_creator = target.id == tenant.creator_id
            if is_creator and target.id not in member_ids:
                await tenant.member.add(target, using_db=connection)
                member_ids.add(target.id)
            if not is_creator and target.id not in member_ids:
                continue
            await target.roles.add(role, using_db=connection)
            restored.append(target.username)

        if not restored:
            raise CustomError(ErrorCode.TENANT_ADMIN_RECOVERY_NO_MEMBER)

    return response_ok({"restored_users": sorted(restored)})


@tenant_bp.post("/switch")
@openapi.definition(
    summary="切换租户",
    description="切换租户",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "租户id"}},
            }
        }
    },
    response={"200": {"description": "切换成功"}},
)
async def switch_tenant(request):
    tenant_id = request.json.get("id")
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    maintenance_mode = await request.app.ctx.cache_client.get(
        request.ctx.session["id"]
    )
    if not can_switch_tenant(maintenance_mode):
        raise CustomError(ErrorCode.MAINTENANCE_MODE_ERROR)

    tenant = await Tenant.get_or_none(id=tenant_id, deleted=False)

    if tenant is None:
        raise CustomError(ErrorCode.TENANT_NOT_FOUND)
    account: Account = request.ctx.session["account_info"]
    if not await tenant.member.filter(id=account.id, deleted=False).exists():
        raise CustomError(ErrorCode.NOT_EXIST_IN_TENANT)

    await _clear_maintenance_mode_for_non_default_tenant(request, tenant.id)
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
    summary="获取当前用户所有的租户",
    description="获取当前用户所有的租户",
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
                    "tenant_id": {"type": "string", "description": "租户id"},
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


@tenant_bp.post("/restore")
@openapi.definition(
    summary="恢复已删除的租户",
    description="恢复已删除的租户（需要租户更新权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "租户id"}},
            }
        }
    },
    response={"200": {"description": "恢复成功"}},
)
@requires_permission("tenant:u")
async def restore_tenant(request):
    """恢复已软删除的租户（阶段三：OpenViking 知识层）。"""
    tenant_id = request.json.get("id")
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant = await Tenant.get_or_none(id=tenant_id)
    if tenant is None:
        raise CustomError(ErrorCode.TENANT_NOT_FOUND)

    if not tenant.deleted:
        return response_ok({"message": "租户未被删除，无需恢复"})

    # 恢复租户
    tenant.deleted = False
    await tenant.save()

    # 恢复知识库凭证（重新签发 key，递增 credential_version）
    try:
        await restore_tenant_knowledge(tenant.id)
    except Exception as e:
        # 知识库恢复失败不阻塞租户恢复
        request.app.ctx.logger.warning(f"Failed to restore knowledge for tenant {tenant.id}: {e}")

    return response_ok({"message": "租户恢复成功"})


@tenant_bp.post("/rotate-knowledge-key")
@openapi.definition(
    summary="轮换租户知识库 API key",
    description="轮换租户知识库 API key（需要租户更新权限，阶段三）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "租户id"}},
            }
        }
    },
    response={"200": {"description": "轮换成功"}},
)
@requires_permission("tenant:u")
async def rotate_knowledge_key(request):
    """轮换租户知识库 API key（阶段三：OpenViking 知识层）。"""
    tenant_id = request.json.get("id")
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant = await Tenant.get_or_none(id=tenant_id, deleted=False)
    if tenant is None:
        raise CustomError(ErrorCode.TENANT_NOT_FOUND)

    try:
        credential = await rotate_tenant_knowledge_key(tenant.id)
        if credential is None:
            return response_ok({"message": "未找到知识库凭证或 OpenViking 未配置"})

        return response_ok({
            "message": "知识库 key 轮换成功",
            "credential_version": credential.credential_version,
        })

    except Exception as e:
        request.app.ctx.logger.error(f"Failed to rotate knowledge key for tenant {tenant.id}: {e}", exc_info=True)
        raise CustomError(ErrorCode.INTERNAL_SERVER_ERROR)
