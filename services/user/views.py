# coding=utf-8
import time
import uuid
from datetime import datetime

import pyotp
from aiocache import caches
from argon2.exceptions import VerificationError, InvalidHashError
from jsonpath import jsonpath
from sanic import Blueprint, response
from sanic.log import logger
from sanic_ext import openapi
from tortoise.exceptions import IntegrityError, DoesNotExist
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authentication import (
    validate_password,
    token_encode,
    refresh_token,
    validate_username,
    token_decode,
)
from libs.security.authorization import requires_permission
from libs.utils import (
    password_hasher,
    ip_in_list,
    get_client_ip,
    validate_ips,
    get_expiration_date,
)
from models import Account, Role, Scene
from settings import (
    AUTHENTICATION_SESSION_EXPIRATION,
    MAX_CHALLENGE_ATTEMPTS,
    SESSION_HTTPONLY,
    SESSION_SAMESITE,
    SESSION_SECURE,
)

user_bp = Blueprint("user", url_prefix="/user")
_all__ = ["user_bp"]


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
            raise CustomError(ErrorCode.InvalidIP)

    payload = await token_decode(request)
    tenant_id = payload.get("tenant_id")

    try:
        mfa_enable = bool(request.json.get("mfa_enable", False)) or False
        account = await Account.create(
            username=validate_username(request.json.get("username", "")),
            password=password_hasher.hash(validate_password(request.json.get("password") or "")),
            display_name=request.json.get("display_name"),
            email=request.json.get("email"),
            phone=request.json.get("phone"),
            disabled=request.json.get("disabled") or False,
            mfa_enable=mfa_enable,
            mfa_key=pyotp.random_base32(32) if mfa_enable else None,
            permit_ip=request.json.get("permit_ip"),
        )
        # 添加角色
        if request.json.get("role_names"):
            roles = await Role.filter(name__in=request.json.get("role_names"), tenant_id=tenant_id).all()
            await account.roles.add(*roles)
        else:
            await account.fetch_related("roles")
        await account.fetch_related("roles")
        res = account.json
        if mfa_enable:
            res["mfa_uri"] = pyotp.totp.TOTP(account.mfa_key).provisioning_uri(name=account.username, issuer_name="AIOE")
        return response_ok(res)
    except IntegrityError:
        raise CustomError(ErrorCode.AccountExists)


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
    page = int(req_json.get("page", 1))
    size = int(req_json.get("size", 15))
    offset = (page - 1) * size
    limit = size
    # 分页查询Account，返回总数和数据
    condition = {"deleted": False, "type": "user"}
    if req_json.get("username"):
        condition["username"] = req_json.get("username")
    total = await Account.filter(**condition).count()
    users = await Account.filter(**condition).offset(offset).limit(limit).all().prefetch_related("roles")

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
            raise CustomError(ErrorCode.InvalidIP)

    payload = await token_decode(request)
    tenant_id = payload.get("tenant_id")

    account = await Account.create(
        username=validate_username(request.json.get("name", "")),
        password=password_hasher.hash(str(uuid.uuid4())),
        type="api",
        display_name=request.json.get("description", ""),
        expiration=datetime.fromtimestamp(int(time.time()) + request.json.get("expiration_time", AUTHENTICATION_SESSION_EXPIRATION)),
        creator=request.ctx.session["account_info"].username,
        permit_ip=request.json.get("permit_ip"),
    )
    # 添加角色
    if request.json.get("role_names"):
        roles = await Role.filter(name__in=request.json.get("role_names"), tenant_id=tenant_id).all()
        await account.roles.add(*roles)

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
                    "keyword": {"type": "string", "description": "搜索时使用"},
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
    page = int(req_json.get("page", 1))
    size = int(req_json.get("size", 15))
    offset = (page - 1) * size
    limit = size
    # 分页查询ApiUserToken，返回总数和数据
    condition = Q(type="api") & Q(deleted=False)
    keyword = req_json.get("keyword")
    if keyword:
        condition = condition & Q(username__icontains=keyword) | Q(display_name=keyword)

    total = await Account.filter(condition).count()
    api_users = await Account.filter(condition).offset(offset).limit(limit).all().prefetch_related("roles")
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
        raise CustomError(ErrorCode.ApiUserNotFound)

    if api_user.username == "system":
        raise CustomError(ErrorCode.SystemUserCannotBeDeleted)

    await request.app.ctx.cache_client.delete(f"account_{api_user.id}")

    api_user.deleted = True
    await api_user.save(update_fields=["deleted"])

    return response_ok("删除成功")


@user_bp.post("/login")
@openapi.definition(
    summary="用户登录",
    description="用户登录系统",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["username", "password"],
                "properties": {
                    "username": {"type": "string", "description": "用户名"},
                    "password": {"type": "string", "description": "密码"},
                },
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
                                    "id": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "username": {"type": "string"},
                                    "display_name": {"type": "string"},
                                    "email": {"type": "string"},
                                    "phone": {"type": "string"},
                                    "disabled": {"type": "boolean"},
                                    "mfa_enable": {"type": "boolean"},
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
async def login(request):
    username = request.json.get("username")
    password = request.json.get("password")
    if not username or not password:
        raise CustomError(ErrorCode.CredentialsError)

    # 防止暴力破解
    user_attempts = await request.app.ctx.cache_client.get(f"{username}_login_attempts")
    if user_attempts is None:
        user_attempts = 0
    if user_attempts > MAX_CHALLENGE_ATTEMPTS:
        raise CustomError(ErrorCode.MaxChallenge)

    client_ip = get_client_ip(request)

    # 防止暴力破解/撞库
    ip_attempts = await request.app.ctx.cache_client.get(f"{client_ip}_login_attempts")
    if ip_attempts is None:
        ip_attempts = 0
    if ip_attempts > MAX_CHALLENGE_ATTEMPTS:
        raise CustomError(ErrorCode.MaxChallenge)

    try:
        account = await Account.get(username=username, deleted=False).prefetch_related("roles")
        if account.permit_ip and ip_in_list(client_ip, account.permit_ip.split(",")) is False:
            raise CustomError(ErrorCode.IpForbidden)
        # 验证密码
        password_hasher.verify(account.password, password)
        if password_hasher.check_needs_rehash(account.password):
            account.password = password_hasher.hash(password)
            await account.save(update_fields=["password"])
        if account.deleted:
            raise CustomError(ErrorCode.AccountNotFound)
        elif account.disabled:
            raise CustomError(ErrorCode.AccountDisabled)
        resp = account.json
        token_id = str(uuid.uuid4())
        token = token_encode(
            account.id,
            account.username,
            account.display_name,
            account.mfa_enable,
            account.tenant_id,
            token_id,
            ip=client_ip,
        )
        await request.app.ctx.cache_client.set(f"account_{account.id}", account, AUTHENTICATION_SESSION_EXPIRATION)
        # resp['aioe-auth'] = token
        # 如果开启了MFA，缓存记录MFA密钥，便于mfa认证
        if account.mfa_enable and account.mfa_key:
            await request.app.ctx.cache_client.set(
                f"mfa_key_{token_id}",
                account.mfa_key,
                AUTHENTICATION_SESSION_EXPIRATION,
            )
            # 如果是开启mfa之后的第一次登录，在mfa还没绑定的情况下，返回mfa_uri，供前端渲染二维码
            if not account.mfa_bind:
                resp["mfa_uri"] = pyotp.totp.TOTP(account.mfa_key).provisioning_uri(name=account.username, issuer_name="AIOE")
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
        raise CustomError(ErrorCode.CredentialsError)
    except (VerificationError, InvalidHashError):
        await request.app.ctx.cache_client.set(f"{username}_login_attempts", user_attempts + 1, 60 * 5)
        await request.app.ctx.cache_client.set(f"{client_ip}_login_attempts", ip_attempts + 1, 60 * 5)
        # await request.app.ctx.cache_client.expire(f"{username}_login_attempts", 60 * 5)
        # await request.app.ctx.cache_client.expire(f"{client_ip}_login_attempts", 60 * 5)
        raise CustomError(ErrorCode.CredentialsError)


@user_bp.post("/local-auth")
@openapi.exclude()
async def local_auth(request):
    if request.ip != "127.0.0.1":
        raise CustomError(ErrorCode.IpForbidden)

    account = await Account.get(username=request.json.get("username", ""))
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
        # todo: 联动oauth2登出
        # request.app.ctx.oauth2.revoke_token(request.ctx.session.get('token'))
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
        raise CustomError(ErrorCode.NONEEDMFA)
    old_token_id = request.ctx.session.get("id", "")
    mfa_key = await request.app.ctx.cache_client.get(f"mfa_key_{old_token_id}")
    totp = pyotp.TOTP(mfa_key)
    code = request.json.get("code")
    if not code or not mfa_key or not totp.verify(code):
        raise CustomError(ErrorCode.MFAError)

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
                data["password"] = password_hasher.hash(validate_password(request.json.get("password") or ""))
            else:
                data[field] = request.json[field]

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
        raise CustomError(ErrorCode.AccountNotFound)

    params = request.json
    need_update = False
    mfa_uri = None
    for p in params:
        # 敏感属性不允许修改
        if p in ["id", "ctime", "utime", "deleted", "name", "mfa_key", "roles"]:
            raise CustomError(ErrorCode.CannotEditSensitiveAttribute)
        # 不能禁用自己
        if p == "disabled" and request.ctx.session["sub"] == user.id:
            raise CustomError(ErrorCode.CannotDisableSelf)
        need_update = True
        if p == "password":
            user.password = password_hasher.hash(validate_password(request.json.get("password") or ""))
        elif p == "mfa_enable":
            if user.mfa_enable == params[p]:
                continue
            if user.oauth_id and params[p]:
                raise CustomError(ErrorCode.OauthUserCannotUseMfa)
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
    if request.json.get("username") == request.ctx.session["account_info"].username:
        raise CustomError(ErrorCode.CannotDeleteSelf)
    if request.json.get("username") == "admin":
        raise CustomError(ErrorCode.CannotDeleteAdmin)
    await Account.filter(username=request.json.get("username")).update(deleted=True)
    return response_ok("用户删除成功")


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
    account = await Account.get(username=request.json.get("username"), deleted=False)
    if not account:
        raise CustomError(ErrorCode.AccountNotFound)
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
    username = request.json.get("username")
    account = await Account.get_or_none(username=username, deleted=False)
    if not account:
        raise CustomError(ErrorCode.AccountNotFound)

    payload = await token_decode(request)
    tenant_id = payload.get("tenant_id")

    role_names = request.json.get("role_names", "").split(",")
    if role_names in [[""], None]:
        await account.roles.clear()
        return response_ok("success")
    roles = await Role.filter(name__in=role_names, deleted=False, tenant_id=tenant_id).all()
    if len(roles) != len(role_names):
        raise CustomError(ErrorCode.RoleNotFound)

    await account.roles.clear()
    await account.roles.add(*roles)
    return response_ok("success")


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
    redirect_url = await request.app.ctx.cache_client.get(state)
    if not redirect_url:
        redirect_url = "https://" + request.host + "#/welcome"
    logger.info(f"oauth2 redirect_url: {redirect_url}")

    token_info = await request.app.ctx.oauth2.get_access_token(code, "https://" + request.host + "/api/user/oauth/callback")
    logger.info(f"token_info: {token_info}")
    access_token = jsonpath(token_info, request.app.ctx.oauth_cfg["token_path"])
    if not access_token:
        raise CustomError(ErrorCode.GetAccessTokenError)

    async with request.app.ctx.oauth2.get_httpx_client() as c:
        resp = await c.get(
            request.app.ctx.oauth_cfg["user_info_uri"],
            headers={"Authorization": f"Bearer {access_token[0]}"},
        )
        user_info = resp.json()
        logger.info(f"user_info: {user_info}")

    username = jsonpath(user_info, request.app.ctx.oauth_cfg["user_id_path"])
    if not username:
        raise CustomError(ErrorCode.GetUserInfoError)
    try:
        account = await Account.get(username=username[0]).prefetch_related("roles")
    except DoesNotExist:
        if request.app.ctx.oauth_cfg["auto_create"] != "true":
            raise CustomError(ErrorCode.AccountNotFound)
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
    payload = await token_decode(request)
    tenant_id = payload.get("tenant_id")

    scene_name = await caches.get("redis_chat").get(f"aioe_current_scene_{tenant_id}")
    if scene_name:
        if isinstance(scene_name, bytes):
            scene_name = scene_name.decode("utf-8")
        return response_ok({"scene": scene_name})

    if not scene_name:
        s = await Scene.get_or_none(deleted=False, status=1, tenant_id=tenant_id)
        if s:
            await caches.get("redis_chat").set(f"aioe_current_scene_{tenant_id}", s.name)
            return response_ok({"scene": s.name})
    return response_ok({"scene": None})


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
    users = await Account.filter(deleted=False, type="user").all().values_list("id", "username", "display_name")
    return response_ok({"users": [{"id": user[0], "username": user[1], "display_name": user[2]} for user in users]})


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
    payload = await token_decode(request)
    tenant_id = payload.get("tenant_id")

    roles = await Role.filter(deleted=False, tenant_id=tenant_id).all().values_list("id", "name")
    return response_ok({"roles": [{"id": role[0], "name": role[1]} for role in roles]})
