# coding=utf-8
import traceback
import uuid

import pyotp
from argon2.exceptions import VerificationError, InvalidHashError
from sanic import Blueprint
from sanic.log import logger
from tortoise.exceptions import ValidationError, IntegrityError, FieldError, DoesNotExist

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authentication import validate_password, token_encode, refresh_token
from libs.security.authorization import requires_permission
from libs.security.utils import password_hasher, get_expiration_date
from models import Account, Role
from settings import AUTHENTICATION_SESSION_EXPIRATION, MAX_CHALLENGE_ATTEMPTS, SESSION_HTTPONLY, SESSION_SAMESITE, SESSION_SECURE, SESSION_DOMAIN

user_bp = Blueprint('user', url_prefix='v1/user')
_all__ = ["user_bp"]


@user_bp.post("/new")
@requires_permission("user:c")
async def new_user(request):
    """
    新建用户

    请求参数
        username: 账号名称
        password: 密码
        phone: 电话号码
        email: 邮箱
        disabled: 是否禁用
        mfa_enable: 是否启用MFA
        mfa_key: MFA密钥
    """
    try:
        mfa_enable = bool(request.json.get('mfa_enable', False)) or False
        account = await Account.create(
            username=request.json.get("username") or '',
            password=password_hasher.hash(validate_password(request.json.get("password") or '')),
            email=request.json.get("email"),
            phone=request.json.get("phone"),
            disabled=request.json.get("disabled") or False,
            mfa_enable=mfa_enable,
            mfa_key=pyotp.random_base32(32) if mfa_enable else None
        )
        logger.info(account.mfa_key)
        logger.info(f"{request.client_ip} 添加账号 {account.username}.")
        res = account.json
        if mfa_enable:
            res['mfa_uri'] = pyotp.totp.TOTP(account.mfa_key).provisioning_uri(name=account.username, issuer_name='AIOE')
        return response_ok(res)
    except (ValidationError, FieldError) as e:
        logger.error(f"{e} | {traceback.format_exc()}")
        raise CustomError(ErrorCode.ParameterError)
    except IntegrityError:
        raise CustomError(ErrorCode.AccountExists)


@user_bp.post("/search")
@requires_permission("user:r")
async def query_user(request):
    """
    删除用户

    请求参数：
        username: 用户名
    """
    user = await Account.filter(name=request.json.get("username"), deleted=False).first()
    if not user:
        raise CustomError(ErrorCode.AccountNotFound)
    return response_ok(user.json)


@user_bp.post("/list")
@requires_permission("user:r")
async def all_users(request):
    """
    获取所有用户信息

    请求参数：
        page: 分页的页码
        size: 分页的大小
    """
    page = int(request.json.get("page", 1))
    page_size = int(request.json.get("size", 10))
    offset = (page - 1) * page_size
    limit = page_size
    users = await Account.filter(deleted=False).offset(offset).limit(limit).all()
    data = []
    for u in users:
        data.append(u.json)
    return response_ok(data)


@user_bp.post("/login")
async def login(request):
    """
    用户登录

    请求参数：
        username: 用户名
        password: 密码
    """
    username = request.json.get("username")
    password = request.json.get("password")
    if not username or not password:
        raise CustomError(ErrorCode.CredentialsError)
    try:
        account = await Account.get(username=username, deleted=False).prefetch_related('roles')
        # 防止暴力破解
        attempts = await request.ctx.cache_client.get(f"{username}_login_attempts")
        if attempts and int(attempts) > MAX_CHALLENGE_ATTEMPTS:
            raise CustomError(ErrorCode.MaxChallenge)
        # 验证密码
        password_hasher.verify(account.password, password)
        if password_hasher.check_needs_rehash(account.password):
            account.password = password_hasher.hash(password)
            await account.save(update_fields=["password"])
        account.validate()
        resp = account.json
        resp['roles'] = [role.name for role in account.roles]
        response = response_ok(resp)
        token_id = str(uuid.uuid4())
        token = token_encode(account, token_id, ip=request.client_ip)
        response.cookies.add_cookie(
            f"aioe_auth",
            token,
            httponly=SESSION_HTTPONLY,
            samesite=SESSION_SAMESITE,
            secure=SESSION_SECURE,
            domain=SESSION_DOMAIN,
            expires=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
        )
        # 如果开启了MFA，记录MFA密钥
        if account.mfa_enable:
            await request.ctx.cache_client.set(f'mfa_key_{id}', account.mfa_key, AUTHENTICATION_SESSION_EXPIRATION)

        return response
    except DoesNotExist:
        raise CustomError(ErrorCode.CredentialsError)
    except (VerificationError, InvalidHashError):
        logger.warning(f"{request.client_ip} 使用 {username} 账号登录失败")
        await request.ctx.cache_client.increment(f"{username}_login_attempts", 1)
        await request.ctx.cache_client.expire(f"{username}_login_attempts", 60 * 5)
        raise CustomError(ErrorCode.CredentialsError)


# @user_bp.post("/logout")
# 
# async def logout(request):
#     """
#     用户登出
#     """
#     # todo: oauth凭据销毁
#     token_info = await oauth_revoke(request, google_oauth)
#     return response_ok(authentication_session.json)


@user_bp.post("/mfa-auth")
async def mfa_auth(request):
    """
    用户二次验证

    请求参数
        code: MFA验证码
    """
    if request.ctx.session.get('mfa_enable') is not True:
        raise CustomError(ErrorCode.NONEEDMFA)
    old_token_id = request.ctx.session.get("id", "")
    mfa_key = await request.ctx.cache_client.get(f'mfa_key_{old_token_id}')
    totp = pyotp.TOTP(mfa_key)
    code = request.json.get('code')
    if not code or not mfa_key or not totp.verify(code):
        raise CustomError(ErrorCode.MFAError)

    # 彻底刷新token，用新的token id
    new_token = refresh_token(request.ctx.session, id=str(uuid.uuid4()), mfa_auth=True)
    response = response_ok('success')
    response.cookies.add_cookie(
        f"aioe_auth",
        new_token,
        httponly=SESSION_HTTPONLY,
        samesite=SESSION_SAMESITE,
        secure=SESSION_SECURE,
        domain=SESSION_DOMAIN,
        expires=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
    )
    await request.ctx.cache_client.delete(f'mfa_key_{old_token_id}')
    return response


@user_bp.post("/update")
async def update_user(request):
    """
    修改用户个人信息（仅能修改自己）

    请求参数：
        email: 邮箱
        phone: 电话
        password: 密码
    """
    userid = request.ctx.session.get('sub')
    account = await Account.filter(id=userid, deleted=False).get()
    if not account:
        raise CustomError(ErrorCode.AccountNotFound)

    for field in ["email", "phone", "password"]:
        if field in request.json:
            setattr(account, field, request.json[field])

    await account.save()

    logger.info(f"{request.client_ip} 更新了账户信息 {account.id}.")
    return response_ok(account.json)


@user_bp.post("/edit")
@requires_permission("user:a")
async def edit_user(request):
    """
    修改任意用户信息（管理员）

    请求参数：
        userid: 用户ID，必须
        email: 邮箱，可选
        phone: 手机号，可选
        password: 密码，可选
        mfa_enable: 是否启用二次认证，可选
        disabled: 是否禁用，可选
        roles: 角色id列表，可选
    """
    try:
        user = await Account.filter(id=request.json.get("userid"), deleted=False).first()
        if not user:
            raise CustomError(ErrorCode.AccountNotFound)

        params = request.json
        need_update = False
        for p in params:
            if p in ['id', 'ctime', 'utime', 'deleted', 'name', 'mfa_key']:
                continue
            need_update = True
            if p == 'password':
                user.password = password_hasher.hash(validate_password(request.json.get("password") or ''))
            else:
                if hasattr(user, p):
                    setattr(user, p, params[p])
        if need_update:
            await user.save()
        return response_ok(user.json)
    except (ValidationError, IntegrityError):
        raise CustomError(ErrorCode.ParameterError)


@user_bp.post("/del")
@requires_permission("user:a")
async def delelte_user(request):
    """
    删除用户

    请求参数：
        userid: 用户ID
    """
    user = await Account.filter(id=request.json.get("userid"), deleted=False).first()
    if not user:
        raise CustomError(ErrorCode.AccountNotFound)
    user.deleted = True
    await user.save()
    return response_ok("用户删除成功")


@user_bp.post("/reset-mfa-key")
@requires_permission("user:a")
async def refresh_mfa_key(request):
    """
    重置MFA密钥

    请求参数：
        userid: 用户ID
    """
    userid = request.json.get('userid')
    account = await Account.filter(id=userid, deleted=False).first()
    if not account:
        raise CustomError(ErrorCode.AccountNotFound)
    account.mfa_key = pyotp.random_base32(32)
    account.mfa_enable = True
    await account.save()
    mfa_uri = pyotp.totp.TOTP(account.mfa_key).provisioning_uri(name=account.username, issuer_name='AIOE')
    return response_ok(mfa_uri)


@user_bp.post("/set-user-role")
@requires_permission("user:a")
async def assign_role_to_user(request):
    """
    给账号分配角色

    请求参数：
        userid: 用户ID
        role_names: 角色ID，多个用英文逗号隔开
    """
    userid = request.json.get('userid')
    account = await Account.filter(id=userid, deleted=False).first()
    if not account:
        raise CustomError(ErrorCode.AccountNotFound)

    roles = request.json.get('role_names', '')
    for r in roles.split(','):
        role = await Role.filter(name=r, deleted=False).get()
        if not role:
            raise CustomError(ErrorCode.RoleNotFound)

        await account.roles.add(role)
    return response_ok("success")
