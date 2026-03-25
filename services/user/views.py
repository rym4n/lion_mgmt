# coding=utf-8
import time
import traceback
import uuid
from datetime import datetime

import jwt
import pyotp
from argon2.exceptions import InvalidHashError, VerificationError
from jsonpath import jsonpath
from sanic import Blueprint, response
from sanic.log import logger
from sanic_ext import openapi
from tortoise.exceptions import DoesNotExist, IntegrityError
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authentication import (
    get_jwt_config,
    get_secure_config_dict,
    refresh_token,
    token_encode,
    validate_password,
    validate_username,
)
from libs.security.authorization import check_permissions, requires_permission
from libs.utils import (
    get_client_ip,
    get_expiration_date,
    ip_in_list,
    password_hasher,
    validate_ips,
)
from models import Account, Role, Scene, Tenant
from settings import (
    AUTHENTICATION_SESSION_EXPIRATION,
    SESSION_HTTPONLY,
    SESSION_SAMESITE,
    SESSION_SECURE,
)

user_bp = Blueprint("user", url_prefix="/user")
__all__ = ["user_bp"]


@user_bp.post("/new")
@openapi.definition(
    summary="新建用户",
    description="创建一个新的用户账户，需要user:c权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["username", "password", "display_name"],
                "properties": {
                    "username": {"type": "string", "description": "账号"},
                    "password": {"type": "string", "description": "密码"},
                    "display_name": {"type": "string", "description": "姓名"},
                    "phone": {"type": "string", "description": "电话号码"},
                    "email": {"type": "string", "description": "邮箱"},
                    "disabled": {"type": "boolean", "description": "是否禁用"},
                    "mfa_enable": {"type": "boolean", "description": "是否启用MFA"},
                    "mfa_key": {"type": "string", "description": "MFA密钥"},
                    "role_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "角色名称列表",
                    },
                    "permit_ip": {
                        "type": "string",
                        "description": "允许访问的IP列表，逗号分隔 192.168.1.1,192.168.1.2 如果为空则为全部ip",
                    },
                    "tenant_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "工作空间列表,注意传入此参数会校验当前账户是否存在工作空间模块权限，不存在则创建失败",
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
                                    "username": {"type": "string"},
                                    "display_name": {"type": "string"},
                                    "email": {"type": "string"},
                                    "phone": {"type": "string"},
                                    "disabled": {"type": "boolean"},
                                    "mfa_enable": {"type": "boolean"},
                                    "mfa_key": {"type": "string"},
                                    "mfa_uri": {"type": "string"},
                                    "permit_ip": {"type": "string"},
                                    "force_change_password": {"type": "boolean"},
                                    "roles": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                                "description": {"type": "string"},
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
@requires_permission("user:c")
async def new_user(request):
    if request.json.get("permit_ip"):
        if validate_ips(request.json["permit_ip"]) is not True:
            raise CustomError(ErrorCode.INVALID_IP)
    t_ids = request.json.get("tenant_ids", [])
    if t_ids:
        await check_permissions(request, "tenant:c")
    tenant_id = t_ids[0] if t_ids else request.ctx.session["tenant_id"]

    try:
        # 密码强度校验
        password = request.json.get("password").strip() or ""
        await validate_password(password)

        mfa_enable = bool(request.json.get("mfa_enable", False)) or False
        account = await Account.create(
            username=validate_username(request.json.get("username", "").strip()),
            password=password_hasher.hash(password),
            display_name=request.json.get("display_name"),
            email=request.json.get("email", "").strip() or None,
            phone=request.json.get("phone", "").strip() or None,
            disabled=bool(request.json.get("disabled", False)) or False,
            mfa_enable=mfa_enable,
            mfa_key=pyotp.random_base32(32) if mfa_enable else None,
            permit_ip=request.json.get("permit_ip", "").strip(),
            tenant_id=tenant_id,
        )
        # 添加角色
        if request.json.get("role_names"):
            roles = await Role.filter(name__in=request.json.get("role_names"), tenant_id=tenant_id, deleted=False).all()
            await account.roles.add(*roles)
        else:
            await account.fetch_related("roles")

        ids = t_ids if t_ids else [request.ctx.session["tenant_id"]]
        tenants = await Tenant.filter(id__in=ids).all()
        await account.member.add(*tenants)

        await account.fetch_related("roles")
        res = account.json
        if mfa_enable:
            res["mfa_uri"] = pyotp.totp.TOTP(account.mfa_key).provisioning_uri(name=account.username, issuer_name="AIOE")
        return response_ok(res)
    except IntegrityError:
        raise CustomError(ErrorCode.ACCOUNT_EXISTS)


@user_bp.post("/list")
@openapi.definition(
    summary="获取所有用户信息",
    description="获取所有用户信息列表，需要user:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "分页的页码，默认1"},
                    "size": {"type": "integer", "description": "分页的大小，默认10"},
                    "username": {"type": "string", "description": "用户名，搜索时使用"},
                    "display_name": {"type": "string", "description": "姓名/展示名，搜索时使用"},
                    "roles": {
                        "description": "角色名称（支持字符串或数组；字符串可用逗号分隔多个）",
                        "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                    },
                    "phone": {"type": "string", "description": "手机号，搜索时使用"},
                    "email": {"type": "string", "description": "邮箱，搜索时使用"},
                    "disabled": {"type": "boolean", "description": "是否禁用"},
                    "mfa_enable": {"type": "boolean", "description": "是否开启MFA"},
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
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total_pages": {"type": "integer"},
                                    "data": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                                "username": {"type": "string"},
                                                "display_name": {"type": "string"},
                                                "email": {"type": "string"},
                                                "phone": {"type": "string"},
                                                "disabled": {"type": "boolean"},
                                                "mfa_enable": {"type": "boolean"},
                                                "permit_ip": {"type": "string"},
                                                "roles": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "name": {"type": "string"},
                                                            "description": {"type": "string"},
                                                        },
                                                    },
                                                },
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
@requires_permission("user:r")
async def all_users(request):
    req_json = request.json or {}
    page = max(1, int(req_json.get("page", 1) or 1))
    size = max(1, min(int(req_json.get("size", 15) or 15), 100))
    offset = (page - 1) * size

    tenant_ids = [request.ctx.session.get("tenant_id")]

    qs = Account.filter(deleted=False, type="user", member__id__in=tenant_ids).distinct()

    username = (req_json.get("username") or "").strip()
    if username:
        qs = qs.filter(username=username)

    display_name = (req_json.get("display_name") or "").strip()
    if display_name:
        qs = qs.filter(display_name=display_name)

    phone = (req_json.get("phone") or "").strip()
    if phone:
        qs = qs.filter(phone=phone)

    email = (req_json.get("email") or "").strip()
    if email:
        qs = qs.filter(email=email)

    disabled = req_json.get("disabled")
    if isinstance(disabled, bool):
        qs = qs.filter(disabled=disabled)

    mfa_enable = req_json.get("mfa_enable")
    if isinstance(mfa_enable, bool):
        qs = qs.filter(mfa_enable=mfa_enable)

    raw_roles = req_json.get("roles")
    role_terms: list[str] = []
    if isinstance(raw_roles, list):
        role_terms = [str(x).strip() for x in raw_roles if str(x).strip()]
    elif isinstance(raw_roles, str):
        role_terms = [x.strip() for x in raw_roles.split(",") if x.strip()]
    elif raw_roles is not None:
        role_terms = [str(raw_roles).strip()] if str(raw_roles).strip() else []

    if role_terms:
        if len(role_terms) == 1:
            qs = qs.filter(roles__name__icontains=role_terms[0], deleted=False)
        else:
            qs = qs.filter(roles__name__in=role_terms, deleted=False)

    total = await qs.count()

    users = await qs.offset(offset).limit(size).prefetch_related("roles")

    return response_ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
            "data": [u.json for u in users],
        }
    )


@user_bp.post("/new-token")
@openapi.definition(
    summary="创建API用户",
    description="创建一个新的API用户，需要user:c权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "expiration_time"],
                "properties": {
                    "name": {"type": "string", "description": "用户名"},
                    "expiration_time": {
                        "type": "integer",
                        "description": "过期时间，单位：秒",
                    },
                    "role_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "角色名称列表，逗号分隔",
                    },
                    "description": {"type": "string", "description": "描述"},
                    "permit_ip": {
                        "type": "string",
                        "description": "允许访问的IP列表，逗号分隔 192.168.1.1,192.168.1.2 如果为空则为全部ip",
                    },
                    "tenant_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "工作空间列表",
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
                                    "username": {"type": "string"},
                                    "display_name": {"type": "string"},
                                    "expiration": {"type": "string"},
                                    "creator": {"type": "string"},
                                    "permit_ip": {"type": "string"},
                                    "token": {"type": "string"},
                                    "roles": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                                "description": {"type": "string"},
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
@requires_permission("user:c")
async def create_api_user(request):
    if request.json.get("permit_ip"):
        if validate_ips(request.json["permit_ip"]) is not True:
            raise CustomError(ErrorCode.INVALID_IP)

    tenant_id = request.ctx.session.get("tenant_id")

    t_ids = request.json.get("tenant_ids", [])

    account = await Account.create(
        username=validate_username(request.json.get("name", "")),
        password=password_hasher.hash(str(uuid.uuid4())),
        type="api",
        display_name=request.json.get("description", ""),
        expiration=datetime.fromtimestamp(int(time.time()) + request.json.get("expiration_time", AUTHENTICATION_SESSION_EXPIRATION)),
        creator=request.ctx.session["account_info"].username,
        permit_ip=request.json.get("permit_ip"),
        tenant_id=t_ids[0] if t_ids else request.ctx.session["tenant_id"],
    )
    # 添加角色
    if request.json.get("role_names"):
        roles = await Role.filter(name__in=request.json.get("role_names"), tenant_id=tenant_id, deleted=False).all()
        await account.roles.add(*roles)

    # 添加工作空间
    ids = t_ids if t_ids else [request.ctx.session["tenant_id"]]
    tenants = await Tenant.filter(id__in=ids, deleted=False).all()
    await account.member.add(*tenants)

    token = token_encode(
        account.id,
        account.username,
        account.display_name,
        account.mfa_enable,
        account.tenant_id,
        str(uuid.uuid4()),
        request.json.get("expiration_time", AUTHENTICATION_SESSION_EXPIRATION),
        account.password,
        permit_ip=account.permit_ip,
    )
    await account.fetch_related("roles")
    await request.app.ctx.cache_client.set(f"account_{account.id}", account, 7 * 24 * 3600)

    data_json = account.api_json
    data_json["token"] = account.id + ":" + token
    return response_ok(data_json)


@user_bp.post("/list-token")
@openapi.definition(
    summary="获取所有API用户信息",
    description="获取所有API用户信息列表，需要user:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "分页的页码，默认1"},
                    "size": {"type": "integer", "description": "分页的大小，默认10"},
                    "username": {"type": "string", "description": "用户名，搜索时使用"},
                    "creator": {"type": "string", "description": "创建人，搜索时使用"},
                    "roles": {
                        "description": "角色名称（支持字符串或数组；字符串可用逗号分隔多个）",
                        "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
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
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total_pages": {"type": "integer"},
                                    "data": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                                "username": {"type": "string"},
                                                "display_name": {"type": "string"},
                                                "expiration": {"type": "string"},
                                                "creator": {"type": "string"},
                                                "permit_ip": {"type": "string"},
                                                "roles": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "name": {"type": "string"},
                                                            "description": {"type": "string"},
                                                        },
                                                    },
                                                },
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
@requires_permission("user:r")
async def list_api_users(request):
    req_json = request.json or {}
    page = max(1, int(req_json.get("page", 1) or 1))
    size = max(1, min(int(req_json.get("size", 15) or 15), 100))
    offset = (page - 1) * size
    # 分页查询ApiUserToken，返回总数和数据
    tenant_id = request.ctx.session.get("tenant_id")
    qs = Account.filter(Q(type="api") & Q(deleted=False) & Q(member__id__in=[tenant_id])).distinct()

    username = (req_json.get("username") or "").strip()
    if username:
        qs = qs.filter(username=username)

    creator = (req_json.get("creator") or "").strip()
    if creator:
        qs = qs.filter(creator=creator)

    raw_roles = req_json.get("roles")
    role_terms: list[str] = []
    if isinstance(raw_roles, list):
        role_terms = [str(x).strip() for x in raw_roles if str(x).strip()]
    elif isinstance(raw_roles, str):
        role_terms = [x.strip() for x in raw_roles.split(",") if x.strip()]
    elif raw_roles is not None:
        role_terms = [str(raw_roles).strip()] if str(raw_roles).strip() else []

    if role_terms:
        if len(role_terms) == 1:
            qs = qs.filter(roles__name__icontains=role_terms[0], deleted=False)
        else:
            qs = qs.filter(roles__name__in=role_terms, deleted=False)

    total = await qs.count()
    api_users = await qs.offset(offset).limit(size).all().prefetch_related("roles")
    data = []
    for u in api_users:
        data.append(u.api_json)

    return response_ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
            "data": data,
        }
    )


@user_bp.post("/del-token")
@openapi.definition(
    summary="删除API用户信息",
    description="删除指定的API用户信息，需要user:d权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "API用户ID"}},
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
@requires_permission("user:d")
async def update_api_user(request):
    api_user = await Account.get(id=request.json.get("id"), deleted=False)
    if not api_user:
        raise CustomError(ErrorCode.API_USER_NOT_FOUND)

    if api_user.username == "system":
        raise CustomError(ErrorCode.SYSTEM_USER_CANNOT_BE_DELETED)

    await request.app.ctx.cache_client.delete(f"account_{api_user.id}")

    api_user.deleted = True
    await api_user.save(update_fields=["deleted"])

    return response_ok("删除成功")


@user_bp.post("/login")
@openapi.definition(
    summary="用户登录",
    description="用户登录系统，验证成功后返回 token",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["username", "password"],
                "properties": {"username": {"type": "string", "description": "用户名"}, "password": {"type": "string", "description": "密码"}},
            }
        }
    },
    response={
        "200": {
            "description": "登录成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "mfa_enable": {"type": "boolean", "description": "是否需要 MFA 验证"},
                                    "mfa_uri": {"type": "string", "description": "MFA 二维码 URI（首次绑定时返回）"},
                                    "force_change_password": {"type": "boolean", "description": "是否需要强制修改密码"}
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
async def login(request):
    username = request.json.get("username", "").strip()
    password = request.json.get("password", "").strip()

    if not username or not password:
        raise CustomError(ErrorCode.CREDENTIALS_ERROR)

    secure_config = await get_secure_config_dict()

    # 防止暴力破解
    user_attempts = await request.app.ctx.cache_client.get(f"{username}_login_attempts")
    if user_attempts is None:
        user_attempts = 0

    # 使用配置中的最大重试次数
    max_retries = secure_config["password_max_retries"]
    lock_duration = secure_config["account_lock_duration"]

    if user_attempts > max_retries:
        raise CustomError(ErrorCode.ACCOUNT_LOCKED)

    client_ip = get_client_ip(request)

    # 防止暴力破解/撞库
    ip_attempts = await request.app.ctx.cache_client.get(f"{client_ip}_login_attempts")
    if ip_attempts is None:
        ip_attempts = 0
    if ip_attempts > max_retries:
        raise CustomError(ErrorCode.ACCOUNT_LOCKED)

    try:
        account = await Account.get(username=username, deleted=False).prefetch_related("roles")
        if account.permit_ip and ip_in_list(client_ip, account.permit_ip.split(",")) is False:
            raise CustomError(ErrorCode.IP_FORBIDDEN)

        # 账号自动冻结检查
        if secure_config["account_auto_freeze_enabled"]:
            if (time.time() - account.last_login_time.timestamp()) > (secure_config["account_auto_freeze_days"] * 86400):
                account.disabled = True
                await account.save(update_fields=["disabled"])
                raise CustomError(ErrorCode.ACCOUNT_DISABLED)

        if account.deleted:
            raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
        elif account.disabled:
            raise CustomError(ErrorCode.ACCOUNT_DISABLED)

        # 验证密码
        password_hasher.verify(account.password, password)

        # 登录成功，清除错误次数
        await request.app.ctx.cache_client.delete(f"{username}_login_attempts")

        resp = {}
        token_id = str(uuid.uuid4())

        # MFA 强制开启检查
        mfa_enabled = secure_config["mfa_enabled"] or account.mfa_enable

        token = token_encode(account.id, account.username, account.display_name, mfa_enabled, account.tenant_id, token_id, ip=client_ip)
        await request.app.ctx.cache_client.set(f"account_{account.id}", account, AUTHENTICATION_SESSION_EXPIRATION)

        # 如果开启了MFA，缓存记录MFA密钥，便于mfa认证
        if mfa_enabled:
            # 如果全局开启，但用户没有 key，生成一个
            if not account.mfa_key:
                account.mfa_key = pyotp.random_base32(32)
                account.mfa_enable = True
                await account.save(update_fields=["mfa_key", "mfa_enable"])

            await request.app.ctx.cache_client.set(
                f"mfa_key_{token_id}",
                account.mfa_key,
                AUTHENTICATION_SESSION_EXPIRATION,
            )
            # 如果是开启mfa之后的第一次登录，在mfa还没绑定的情况下，返回mfa_uri，供前端渲染二维码
            if not account.mfa_bind:
                resp["mfa_uri"] = pyotp.totp.TOTP(account.mfa_key).provisioning_uri(name=account.username, issuer_name="AIOE")

        resp["mfa_enable"] = mfa_enabled

        # 首次登录强制修改密码
        if secure_config["force_change_password_first_login"]:
            if not account.last_login_time:
                resp["force_change_password"] = True

        if not resp.get("force_change_password"):
            account.last_login_time = datetime.now()
            await account.save(update_fields=["last_login_time"])

        tmp_resp = response_ok(resp)
        tmp_resp.cookies.add_cookie(
            "aioe-auth",
            token,
            httponly=SESSION_HTTPONLY,
            samesite=SESSION_SAMESITE,
            secure=SESSION_SECURE,
            expires=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
        )
        return tmp_resp
    except DoesNotExist:
        raise CustomError(ErrorCode.CREDENTIALS_ERROR)
    except (VerificationError, InvalidHashError):
        await request.app.ctx.cache_client.set(f"{username}_login_attempts", user_attempts + 1, lock_duration)
        await request.app.ctx.cache_client.set(f"{client_ip}_login_attempts", ip_attempts + 1, lock_duration)
        raise CustomError(ErrorCode.CREDENTIALS_ERROR)


@user_bp.post("/user-info")
@openapi.definition(
    summary="获取当前登录用户信息",
    description="获取当前登录用户的详细信息，包括角色和权限",
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
                                    "id": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "username": {"type": "string"},
                                    "display_name": {"type": "string"},
                                    "email": {"type": "string"},
                                    "phone": {"type": "string"},
                                    "disabled": {"type": "boolean"},
                                    "mfa_enable": {"type": "boolean"},
                                    "permit_ip": {"type": "string"},
                                    "tenant_id": {"type": "string"},
                                    "roles": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                                "description": {"type": "string"},
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
async def get_user_info(request):
    """获取当前登录用户的详细信息"""
    account = request.ctx.session.get("account_info")
    if not account:
        raise CustomError(ErrorCode.SESSION_INVALID)

    # 重新从数据库获取最新的用户信息（包括角色）
    account = await Account.get(id=account.id, deleted=False).prefetch_related("roles")
    resp = account.json
    resp["tenant_id"] = request.ctx.session.get("tenant_id")
    return response_ok()


@user_bp.post("/local-auth")
@openapi.exclude()
async def local_auth(request):
    if request.ip != "127.0.0.1":
        raise CustomError(ErrorCode.IP_FORBIDDEN)

    username = request.json.get("username", "").strip()

    account = await Account.get(username=username, tenant_id="8bce552d-c27c-41d4-8a1b-8c5bf38dfe18", deleted=False)
    if not account:
        raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
    token = token_encode(
        account.id,
        account.username,
        account.display_name,
        False,
        account.tenant_id,
        str(uuid.uuid4()),
        AUTHENTICATION_SESSION_EXPIRATION,
        account.password,
    )
    return response_ok({"token": account.id + ":" + token})


@user_bp.post("/refresh-token")
@openapi.definition(
    summary="刷新token",
    description="刷新用户认证token",
    response={
        "200": {
            "description": "刷新成功",
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
async def refresh_session_token(request):
    new_token = refresh_token(request.ctx.session)
    account = await request.app.ctx.cache_client.get(f"account_{request.ctx.session['sub']}")
    if not account:
        account = await Account.get(id=request.ctx.session["sub"]).prefetch_related("roles")
    await request.app.ctx.cache_client.set(
        f"account_{request.ctx.session['sub']}",
        account,
        AUTHENTICATION_SESSION_EXPIRATION,
    )

    # 更新维护模式redis缓存
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    await request.app.ctx.cache_client.set(request.ctx.session["id"], maintenance_mode, AUTHENTICATION_SESSION_EXPIRATION)

    resp_ok = response_ok("success")
    resp_ok.cookies.add_cookie(
        "aioe-auth",
        new_token,
        httponly=SESSION_HTTPONLY,
        samesite=SESSION_SAMESITE,
        secure=SESSION_SECURE,
        expires=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
    )
    return resp_ok


@user_bp.post("/logout")
@openapi.definition(
    summary="用户登出",
    description="用户登出系统",
    response={
        "200": {
            "description": "登出成功",
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
async def logout(request):
    await request.app.ctx.cache_client.delete(f"account_{request.ctx.session['sub']}")

    if request.app.ctx.oauth_cfg.get("use_oauth2") == "true":
        resp = response.redirect(f"https://{request.host}/api/user/oauth/login?redirect=https://{request.host}/%23%2Fwelcome")
    else:
        resp = response_ok("登出成功")

    resp.cookies.add_cookie(
        "aioe-auth",
        "",
        httponly=SESSION_HTTPONLY,
        samesite=SESSION_SAMESITE,
        secure=SESSION_SECURE,
        expires=get_expiration_date(-60),
    )
    return resp


@user_bp.post("/mfa-auth")
@openapi.definition(
    summary="用户二次验证",
    description="用户MFA二次验证",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["code"],
                "properties": {"code": {"type": "string", "description": "MFA验证码"}},
            }
        }
    },
    response={
        "200": {
            "description": "验证成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {"aioe-token": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
async def mfa_auth(request):
    if request.ctx.session.get("mfa_enable") is not True:
        raise CustomError(ErrorCode.NO_NEED_MFA)
    old_token_id = request.ctx.session.get("id", "")
    mfa_key = await request.app.ctx.cache_client.get(f"mfa_key_{old_token_id}")
    totp = pyotp.TOTP(mfa_key)
    code = request.json.get("code")
    if not code or not mfa_key or not totp.verify(code):
        raise CustomError(ErrorCode.MFA_ERROR)

    await Account.filter(id=request.ctx.session.get("sub")).update(mfa_bind=True)
    # 彻底刷新token，用新的token id
    new_token = refresh_token(request.ctx.session, id=str(uuid.uuid4()), mfa_auth=True)
    resp = response_ok({"aioe-token": new_token})
    resp.cookies.add_cookie(
        "aioe-auth",
        new_token,
        httponly=SESSION_HTTPONLY,
        samesite=SESSION_SAMESITE,
        secure=SESSION_SECURE,
        expires=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
    )
    await request.app.ctx.cache_client.delete(f"mfa_key_{old_token_id}")
    return resp


@user_bp.post("/update")
@openapi.definition(
    summary="修改用户个人信息",
    description="在个人中心修改用户个人信息（仅能修改自己）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "邮箱"},
                    "phone": {"type": "string", "description": "电话"},
                    "password": {"type": "string", "description": "密码"},
                    "permit_ip": {
                        "type": "string",
                        "description": "允许登录的IP地址，支持多个IP或CIDR格式（逗号分隔），例如 192.168.1.1,10.0.2.0/24",
                    },
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
async def update_user(request):
    data = dict()
    for field in ["email", "phone", "password", "permit_ip"]:
        if field in request.json:
            if field == "password":
                # 密码强度校验
                password = request.json.get("password").strip() or ""
                await validate_password(password)
                data["password"] = password_hasher.hash(password)
            else:
                data[field] = request.json[field]

    # 如果是首次登录强制改密的话，需要刷新认证时间
    if request.ctx.session["account_info"].last_login_time is None:
        data["last_login_time"] = datetime.now()
    await Account.filter(id=request.ctx.session.get("sub")).update(**data)

    return response_ok("success")


@user_bp.post("/edit")
@openapi.definition(
    summary="修改任意用户信息",
    description="修改任意用户信息，需要user:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["username"],
                "properties": {
                    "username": {"type": "string", "description": "用户名"},
                    "email": {"type": "string", "description": "邮箱"},
                    "phone": {"type": "string", "description": "手机号"},
                    "password": {"type": "string", "description": "密码"},
                    "mfa_enable": {
                        "type": "boolean",
                        "description": "是否启用二次认证",
                    },
                    "disabled": {"type": "boolean", "description": "是否禁用"},
                    "permit_ip": {
                        "type": "string",
                        "description": "允许登录的IP地址，支持多个IP或CIDR格式（逗号分隔），例如 192.168.1.1,10.0.2.0/24",
                    },
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
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "username": {"type": "string"},
                                    "display_name": {"type": "string"},
                                    "email": {"type": "string"},
                                    "phone": {"type": "string"},
                                    "disabled": {"type": "boolean"},
                                    "mfa_enable": {"type": "boolean"},
                                    "mfa_key": {"type": "string"},
                                    "mfa_uri": {"type": "string"},
                                    "permit_ip": {"type": "string"},
                                    "roles": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                                "description": {"type": "string"},
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
@requires_permission("user:u")
async def edit_user(request):
    user = await Account.get(username=request.json.get("username"), deleted=False).prefetch_related("roles")
    if not user:
        raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)

    params = request.json
    need_update = False
    mfa_uri = None
    for p in params:
        # 敏感属性不允许修改
        if p in ["id", "ctime", "utime", "deleted", "name", "mfa_key", "roles"]:
            raise CustomError(ErrorCode.CANNOT_EDIT_SENSITIVE_ATTRIBUTE)
        # 不能禁用自己
        if p == "disabled" and request.ctx.session["sub"] == user.id:
            raise CustomError(ErrorCode.CANNOT_DISABLE_SELF)
        need_update = True
        if p == "password":
            # 密码强度校验
            password = request.json.get("password").strip() or ""
            await validate_password(password)
            user.password = password_hasher.hash(password)
        elif p == "mfa_enable":
            if user.mfa_enable == params[p]:
                continue
            if user.oauth_id and params[p]:
                raise CustomError(ErrorCode.OAUTH_USER_CANNOT_USE_MFA)
            user.mfa_enable = params[p]
            user.mfa_key = pyotp.random_base32(32) if user.mfa_enable else None
            user.mfa_bind = False
            if user.mfa_key:
                # 关 -> 开
                mfa_uri = pyotp.totp.TOTP(user.mfa_key).provisioning_uri(name=user.username, issuer_name="AIOE")
        else:
            if hasattr(user, p):
                setattr(user, p, params[p])
    if need_update:
        await user.save()
    res = user.json
    if mfa_uri:
        res["mfa_uri"] = mfa_uri
    return response_ok(res)


@user_bp.post("/del")
@openapi.definition(
    summary="删除用户",
    description="删除指定用户，需要user:d权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["username"],
                "properties": {"username": {"type": "string", "description": "用户名"}},
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
@requires_permission("user:d")
async def delelte_user(request):
    username = request.json.get("username", "").strip()
    if username == request.ctx.session["account_info"].username:
        raise CustomError(ErrorCode.CANNOT_DELETE_SELF)
    if username == "admin":
        raise CustomError(ErrorCode.CANNOT_DELETE_ADMIN)

    await Account.filter(username=username, deleted=False, tenant_id=request.ctx.session["account_info"].tenant_id).update(deleted=True)
    return response_ok("success")


@user_bp.post("/reset-mfa-key")
@openapi.definition(
    summary="重置MFA密钥",
    description="重置指定用户的MFA密钥，需要user:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["username"],
                "properties": {"username": {"type": "string", "description": "用户名"}},
            }
        }
    },
    response={
        "200": {
            "description": "重置成功",
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
@requires_permission("user:u")
async def refresh_mfa_key(request):
    username = request.json.get("username", "").strip()
    account = await Account.get_or_none(username=username, deleted=False)
    if not account:
        raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
    account.mfa_key = pyotp.random_base32(32)
    account.mfa_enable = True
    await account.save()
    mfa_uri = pyotp.totp.TOTP(account.mfa_key).provisioning_uri(name=account.username, issuer_name="AIOE")
    return response_ok(mfa_uri)


@user_bp.post("/set-user-role")
@openapi.definition(
    summary="给账号分配角色",
    description="给指定账号分配角色，需要user:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["username", "role_names"],
                "properties": {
                    "username": {"type": "string", "description": "用户名"},
                    "role_names": {
                        "type": "string",
                        "description": "角色名称，多个用英文逗号隔开",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "分配成功",
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
@requires_permission("user:u")
async def assign_role_to_user(request):
    username = request.json.get("username", "").strip()
    account = await Account.get_or_none(username=username, deleted=False)
    if not account:
        raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)

    tenant_id = request.ctx.session.get("tenant_id")

    role_names = request.json.get("role_names", "").split(",")
    if not role_names:
        await account.roles.clear()
        return response_ok({"username": username, "roles": []})
    roles = await Role.filter(name__in=role_names, deleted=False, tenant_id=tenant_id).all()
    if len(roles) != len(role_names):
        raise CustomError(ErrorCode.ROLE_NOT_FOUND)

    await account.roles.clear()
    await account.roles.add(*roles)
    return response_ok({"username": username, "roles": role_names})


@user_bp.get("/iframe-login")
@openapi.definition(
    summary="iframe认证登录",
    description="通过第三方JWT Token进行认证，认证成功后写入Cookie并重定向",
    parameter=[
        {
            "name": "token",
            "location": "query",
            "description": "第三方JWT Token",
            "required": True,
            "schema": {"type": "string"},
        },
        {
            "name": "redirect",
            "location": "query",
            "description": "认证成功后的重定向地址",
            "required": False,
            "schema": {"type": "string"},
        },
    ],
    response={"302": {"description": "重定向到目标页面"}},
)
async def iframe_login(request):
    token = request.args.get("token")
    redirect_uri = request.args.get("redirect")

    if not token or not redirect_uri:
        raise CustomError(ErrorCode.PARAM_ERROR)

    cfg = await get_jwt_config()
    if cfg.get("jwt_enabled") != "true":
        raise CustomError(ErrorCode.SESSION_INVALID)

    algo = cfg.get("jwt_algorithm", "HS256")
    secret = cfg.get("jwt_secret", "")
    user_path = cfg.get("jwt_user_path", "")

    if not all([algo, secret, user_path]):
        logger.warning(f"JWT config incomplete: algo={algo}, secret=***, user_path={user_path}")
        raise CustomError(ErrorCode.SESSION_INVALID)

    try:
        decode_key = secret
        if algo == "RS256":
            if not secret.strip().startswith("-----BEGIN PUBLIC KEY-----"):
                secret = f"-----BEGIN PUBLIC KEY-----\n{secret.strip()}\n-----END PUBLIC KEY-----"
            decode_key = secret.encode("utf-8")

        payload = jwt.decode(token, decode_key, algorithms=[algo])
        username = jsonpath(payload, user_path)
        if not username:
            logger.warning(f"JWT user path {user_path} not found in payload")
            raise CustomError(ErrorCode.SESSION_INVALID)

        jwt_tenant_id = payload.get("tenant_id")

        # 4. 查找或自动创建用户
        try:
            account = await Account.get(username=username[0], deleted=False).prefetch_related("roles")
        except DoesNotExist:
            if cfg.get("jwt_auto_create") != "true":
                raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)

            # 自动创建用户逻辑
            account = await Account.create(
                username=username[0],
                oauth_id=username[0],
                password="",
                display_name=None,
                email=None,
                phone=None,
                disabled=False,
                mfa_enable=False,
                mfa_key=None,
                creator="auto",
                permit_ip=None,
            )

            # 分配默认租户
            tenant = await Tenant.get_or_none(id=account.tenant_id)
            if tenant:
                await tenant.member.add(account)

            default_tenants = cfg.get("jwt_default_tenants", "").split(",")
            if default_tenants not in [[""], [], None, ""]:
                tenants_by_id = await Tenant.filter(id__in=default_tenants).all()
                if tenants_by_id:
                    account.tenant_id = tenants_by_id[0].id
                    await account.save(update_fields=["tenant_id"])
                    for t in tenants_by_id:
                        await t.member.add(account)

            # 分配默认角色
            default_roles = cfg.get("jwt_default_roles", "").split(",")
            if default_roles:
                roles = await Role.filter(id__in=default_roles, tenant_id=account.tenant_id).all()
                if roles:
                    await account.roles.add(*roles)

            # 无论是否分配角色，都需要获取关联关系
            await account.fetch_related("roles")

        # 5. 生成系统Token
        target_tenant_id = account.tenant_id
        if jwt_tenant_id:
            if await Tenant.filter(id=jwt_tenant_id, deleted=False).exists():
                target_tenant_id = jwt_tenant_id

        aioe_token = token_encode(
            account.id,
            account.username,
            account.display_name,
            account.mfa_enable,
            target_tenant_id,
            str(uuid.uuid4()),
        )

        # 6. 缓存用户信息
        await request.app.ctx.cache_client.set(f"account_{account.id}", account, AUTHENTICATION_SESSION_EXPIRATION)

        # 7. 构造响应并重定向
        resp = response.redirect(redirect_uri)

        # 添加 CORS 头以支持跨域 iframe 场景
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"

        resp.cookies.add_cookie(
            "aioe-auth",
            aioe_token,
            httponly=SESSION_HTTPONLY,
            samesite=SESSION_SAMESITE,
            secure=SESSION_SECURE,
            partitioned=True,
            host_prefix=True,
            expires=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
        )
        return resp

    except Exception as e:
        logger.warning(f"JWT verify failed: {e} | {traceback.format_exc()}")
        raise CustomError(ErrorCode.SESSION_INVALID)


@user_bp.get("/oauth/login")
@openapi.definition(
    summary="SSO登录",
    description="重定向用户到SSO登录页",
    parameter=[
        {
            "name": "redirect",
            "location": "query",
            "description": "重定向地址",
            "required": False,
            "schema": {"type": "string"},
        }
    ],
    response={"302": {"description": "重定向到SSO登录页面"}},
)
async def oauth2_login(request):
    redirect_uri = request.args.get("redirect", "https://" + request.host + "#/welcome")
    state = str(uuid.uuid4())
    logger.info(redirect_uri)
    await request.app.ctx.cache_client.set(state, redirect_uri, 300)
    cb_url = "https://" + request.host + "/api/user/oauth/callback"
    auth_url = await request.app.ctx.oauth2.get_authorization_url(cb_url, state=state)
    return response.redirect(auth_url)


@user_bp.get("/oauth/callback")
@openapi.definition(
    summary="SSO登录回调",
    description="SSO登录回调接口",
    parameter=[
        {
            "name": "code",
            "location": "query",
            "description": "授权码",
            "required": True,
            "schema": {"type": "string"},
        },
        {
            "name": "state",
            "location": "query",
            "description": "状态码",
            "required": True,
            "schema": {"type": "string"},
        },
    ],
    response={"302": {"description": "重定向到应用页面"}},
)
async def oauth2_callback(request):
    code = request.args.get("code")
    state = request.args.get("state")
    logger.info(f"oauth2 callback: code={code}, state={state}")
    logger.info(f"oauth2 config: {request.app.ctx.oauth_cfg}")
    cb_url = "https://" + request.host + "/api/user/oauth/callback"
    logger.info(f"get_access_token: code={code}, redirect_uri={cb_url}")
    try:
        token_info = await request.app.ctx.oauth2.get_access_token(code, cb_url)
    except Exception as e:
        logger.error(f"{e} | {traceback.format_exc()}")
        raise CustomError(ErrorCode.GET_ACCESS_TOKEN_ERROR)
    logger.info(f"user token: {token_info}")
    access_token = jsonpath(token_info, request.app.ctx.oauth_cfg["token_path"])
    if not access_token:
        raise CustomError(ErrorCode.GET_ACCESS_TOKEN_ERROR)

    async with request.app.ctx.oauth2.get_httpx_client() as c:
        resp = await c.get(
            request.app.ctx.oauth_cfg["user_info_uri"],
            headers={"Authorization": f"Bearer {access_token[0]}"},
        )
        user_info = resp.json()
        logger.info(f"user_info: {user_info}")

    username = jsonpath(user_info, request.app.ctx.oauth_cfg["user_id_path"])
    if not username:
        raise CustomError(ErrorCode.GET_USER_INFO_ERROR)
    try:
        account = await Account.get(username=username[0], deleted=False).prefetch_related("roles")
    except DoesNotExist:
        if request.app.ctx.oauth_cfg["auto_create"] != "true":
            raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
        # 不存在自动新建
        account = await Account.create(
            username=username[0],
            oauth_id=username[0],
            password="",
            display_name=(jsonpath(user_info, request.app.ctx.oauth_cfg.get("display_name_path")) or [None])[0],
            email=(jsonpath(user_info, request.app.ctx.oauth_cfg.get("email_path")) or [None])[0],
            phone=(jsonpath(user_info, request.app.ctx.oauth_cfg.get("phone_path")) or [None])[0],
            disabled=False,
            mfa_enable=False,
            mfa_key=None,
            creator="auto",
            permit_ip=None,
        )

        logger.info(f"account: {account.tenant_id}")
        tenant = await Tenant.get_or_none(id=account.tenant_id)
        logger.info(f"tenant: {tenant}")
        if tenant is not None:
            await tenant.member.add(account)
        else:
            raise CustomError(ErrorCode.DEFAULT_TENANT_NOT_FOUND)

        default_tenants = request.app.ctx.oauth_cfg.get("default_tenants", "").split(",")
        logger.info(f"default_tenants: {default_tenants}")
        if default_tenants not in [[""], [], None, ""]:
            tenants_by_id = await Tenant.filter(id__in=default_tenants).all()
            if tenants_by_id:
                account.tenant_id = tenants_by_id[0].id
                await account.save(update_fields=["tenant_id"])
                for t in tenants_by_id:
                    await t.member.add(account)
            else:
                raise CustomError(ErrorCode.DEFAULT_TENANT_NOT_FOUND)

        default_roles = request.app.ctx.oauth_cfg.get("default_roles", "").split(",")
        if default_roles:
            roles = await Role.filter(id__in=default_roles, tenant_id=account.tenant_id).all()
            if roles:
                await account.roles.add(*roles)
            else:
                raise CustomError(ErrorCode.DEFAULT_ROLE_NOT_FOUND)
        else:
            await account.fetch_related("roles")

    aioe_token = token_encode(
        account.id,
        account.username,
        account.display_name,
        account.mfa_enable,
        account.tenant_id,
        str(uuid.uuid4()),
    )
    await request.app.ctx.cache_client.set(f"account_{account.id}", account, AUTHENTICATION_SESSION_EXPIRATION)

    redirect_url = await request.app.ctx.cache_client.get(state)
    if not redirect_url:
        redirect_url = "https://" + request.host + "#/welcome"
    logger.info(f"用户最终访问地址: {redirect_url}")
    resp = response.redirect(redirect_url)
    resp.cookies.add_cookie(
        "aioe-auth",
        aioe_token,
        httponly=SESSION_HTTPONLY,
        samesite=SESSION_SAMESITE,
        secure=SESSION_SECURE,
        expires=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
    )
    return resp


@user_bp.post("/scene-list")
@openapi.definition(
    summary="获取当前场景名称",
    description="获取当前场景名称",
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
                                "properties": {"scene": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
async def init_scene_list(request):
    tenant_id = request.ctx.session.get("tenant_id")

    s = await Scene.get_or_none(deleted=False, status=1, tenant_id=tenant_id)
    return response_ok({"scene": s.name if s else ""})


@user_bp.post("/account-list")
@openapi.definition(
    summary="获取用户名称、id",
    description="获取所有用户名称和ID列表",
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
                                    "users": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "username": {"type": "string"},
                                                "display_name": {"type": "string"},
                                            },
                                        },
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
async def init_account_list(request):
    tenant = await Tenant.get_or_none(deleted=False, id=request.ctx.session["tenant_id"]).prefetch_related("member")
    data = []
    for user in tenant.member:
        if user.deleted is False and user.type == "user":
            data.append({"id": user.id, "username": user.username, "display_name": user.display_name})
    return response_ok({"users": data})


@user_bp.post("/role-list")
@openapi.definition(
    summary="获取角色名称、id",
    description="获取所有角色名称和ID列表",
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
                                    "roles": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                            },
                                        },
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
async def init_role_list(request):
    tenant_id = request.ctx.session.get("tenant_id")

    roles = await Role.filter(deleted=False, tenant_id=tenant_id).all().values_list("id", "name")
    return response_ok({"roles": [{"id": role[0], "name": role[1]} for role in roles]})


@user_bp.post("/default-tenant")
@openapi.definition(
    summary="设置用户默认tenant_id",
    description="设置用户默认tenant_id",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "工作空间id"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "设置成功",
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
async def set_default_tenant(request):
    req = request.json or {}
    tenant_id = (req.get("tenant_id") or "").strip()
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    account = request.ctx.session.get("account_info")
    if not account.id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    tenant_exists = await Tenant.filter(id=tenant_id, deleted=False).exists()
    if not tenant_exists:
        raise CustomError(ErrorCode.TENANT_NOT_FOUND)

    in_tenant = await Account.filter(
        id=account.id,
        deleted=False,
        type="user",
        member__id=tenant_id,
    ).exists()
    if not in_tenant:
        raise CustomError(ErrorCode.NOT_EXIST_IN_TENANT)
    await Account.filter(id=account.id).update(tenant_id=tenant_id)
    return response_ok("设置成功")


@user_bp.post("/details")
@openapi.definition(
    summary="获取用户详情",
    description="获取用户详情",
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
            "description": "获取成功",
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
async def get_user_details(request):
    account = request.ctx.session["account_info"]
    return response_ok(account.json)


@user_bp.post("/tenant-list")
@openapi.definition(
    summary="获取当前用户所在的工作空间列表",
    description="获取当前用户所在的工作空间列表",
    response={
        "200": {
            "description": "Success",
            "content": {"application/json": {"example": {"tenants": [], "total": 0, "page": 1, "size": 15, "total_pages": 0}}},
        }
    },
)
async def get_tenant_list(request):
    account = request.ctx.session["account_info"]

    tenants = await account.member.all()

    return response_ok([{"id": tenant.id, "name": tenant.name} for tenant in tenants])
