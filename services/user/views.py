# coding=utf-8
import uuid

import pyotp
from argon2.exceptions import VerificationError, InvalidHashError
from sanic import Blueprint
from tortoise.exceptions import ValidationError, IntegrityError, DoesNotExist
from websockets.legacy.server import HTTPResponse

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authentication import validate_password, token_encode, refresh_token
from libs.security.authorization import requires_permission
from libs.security.utils import password_hasher
from models import Account, Role
from settings import AUTHENTICATION_SESSION_EXPIRATION, MAX_CHALLENGE_ATTEMPTS

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
        phone: 可选，电话号码
        email: 可选，邮箱
        disabled: 可选，是否禁用
        mfa_enable: 可选，是否启用MFA
        mfa_key: 可选，MFA密钥
        role_names: 可选，角色名称列表，逗号分隔
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
        await account.fetch_related('roles')
        if request.json.get("role_names"):
            roles = await Role.filter(name__in=request.json.get("role_names")).all()
            await account.roles.add(*roles)
        res = account.json
        if mfa_enable:
            res['mfa_uri'] = pyotp.totp.TOTP(account.mfa_key).provisioning_uri(name=account.username, issuer_name='AIOE')
        return response_ok(res)
    except IntegrityError:
        raise CustomError(ErrorCode.AccountExists)


@user_bp.post("/list")
@requires_permission("user:r")
async def all_users(request):
    """
    获取所有用户信息

    请求参数：
        page: 可选，分页的页码，默认1
        size: 可选，分页的大小，默认10
        username: 可选，用户名，搜索时使用
    """
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 10))
    offset = (page - 1) * size
    limit = size
    # 分页查询Account，返回总数和数据
    condition = {
        "deleted": False
    }
    if request.json.get("username"):
        condition["username"] = request.json.get("username")
    total = await Account.filter(**condition).count()
    users = await Account.filter(**condition).offset(offset).limit(limit).all().prefetch_related('roles')

    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": [u.json for u in users]
    })


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
    # 防止暴力破解
    user_attempts = await request.ctx.cache_client.get(f"{username}_login_attempts")
    if user_attempts is not None and int(user_attempts) > MAX_CHALLENGE_ATTEMPTS:
        raise CustomError(ErrorCode.MaxChallenge)
    client_ip = request.client_ip
    ip_attempts = await request.ctx.cache_client.get(f"{client_ip}_login_attempts")
    if ip_attempts is not None and int(ip_attempts) > MAX_CHALLENGE_ATTEMPTS:
        raise CustomError(ErrorCode.MaxChallenge)
    try:
        account = await Account.get(username=username, deleted=False).prefetch_related('roles')
        # 验证密码
        password_hasher.verify(account.password, password)
        if password_hasher.check_needs_rehash(account.password):
            account.password = password_hasher.hash(password)
            await account.save(update_fields=["password"])
        account.validate()
        resp = account.json
        token_id = str(uuid.uuid4())
        token = token_encode(account.id, account.username, account.mfa_enable, token_id, ip=request.client_ip)
        resp['aioe-auth'] = token
        # response.cookies.add_cookie(
        #     f"aioe_auth",
        #     token,
        #     httponly=SESSION_HTTPONLY,
        #     samesite=SESSION_SAMESITE,
        #     secure=SESSION_SECURE,
        #     domain=SESSION_DOMAIN,
        #     expires=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
        # )
        # 如果开启了MFA，记录MFA密钥
        if account.mfa_enable:
            await request.ctx.cache_client.set(f'mfa_key_{id}', account.mfa_key, AUTHENTICATION_SESSION_EXPIRATION)

        return response_ok(resp)
    except DoesNotExist:
        raise CustomError(ErrorCode.CredentialsError)
    except (VerificationError, InvalidHashError):
        await request.ctx.cache_client.increment(f"{username}_login_attempts", 1)
        await request.ctx.cache_client.increment(f"{client_ip}_login_attempts", 1)
        await request.ctx.cache_client.expire(f"{username}_login_attempts", 60 * 5)
        await request.ctx.cache_client.expire(f"{client_ip}_login_attempts", 60 * 5)
        raise CustomError(ErrorCode.CredentialsError)


@user_bp.post("/refresh-token")
async def refresh_token(request):
    """
    刷新token
    """
    token = token_encode(request.ctx.session['sub'], request.ctx.session['username'], request.ctx.session['mfa_enable'],
                         request.ctx.session['id'], ip=request.client_ip)
    resp = {
        "aioe-auth": token
    }
    return response_ok(resp)


@user_bp.post("/logout")
async def logout(request):
    """
    用户登出
    """
    # todo: oauth凭据销毁
    # token_info = await oauth_revoke(request, google_oauth)
    return HTTPResponse(status=302, headers={"Location": "/login"})


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
    response = response_ok({'aioe-token': new_token})
    # response.cookies.add_cookie(
    #     f"aioe_auth",
    #     new_token,
    #     httponly=SESSION_HTTPONLY,
    #     samesite=SESSION_SAMESITE,
    #     secure=SESSION_SECURE,
    #     domain=SESSION_DOMAIN,
    #     expires=get_expiration_date(AUTHENTICATION_SESSION_EXPIRATION),
    # )
    await request.ctx.cache_client.delete(f'mfa_key_{old_token_id}')
    return response


@user_bp.post("/update")
async def update_user(request):
    """
    在个人中心修改用户个人信息（仅能修改自己）

    请求参数：
        email: 可选，邮箱
        phone: 可选，电话
        password: 可选，密码
    """
    userid = request.ctx.session.get('sub')
    account = await Account.get(id=userid, deleted=False)
    if not account:
        raise CustomError(ErrorCode.AccountNotFound)

    for field in ["email", "phone", "password"]:
        if field in request.json:
            if field == "password":
                account.password = password_hasher.hash(validate_password(request.json.get("password") or ''))
            else:
                setattr(account, field, request.json[field])

    await account.save()

    return response_ok(account.json)


@user_bp.post("/edit")
@requires_permission("user:a")
async def edit_user(request):
    """
    修改任意用户信息（需要user:a权限）

    请求参数：
        username: 必填，用户名
        email: 可选，邮箱
        phone: 可选，手机号
        password: 可选，密码
        mfa_enable: 可选，是否启用二次认证
        disabled: 可选，是否禁用
    """
    try:
        user = await Account.get(username=request.json.get("username"), deleted=False)
        if not user:
            raise CustomError(ErrorCode.AccountNotFound)

        params = request.json
        need_update = False
        mfa_uri = None
        for p in params:
            # 敏感属性不允许修改
            if p in ['id', 'ctime', 'utime', 'deleted', 'name', 'mfa_key', 'roles']:
                continue
            # 不能禁用自己
            if p == 'disabled' and request.ctx.session['sub'] == user.id:
                continue
            need_update = True
            if p == 'password':
                user.password = password_hasher.hash(validate_password(request.json.get("password") or ''))
            elif p == 'mfa_enable':
                mfa_uri = pyotp.totp.TOTP(user.mfa_key).provisioning_uri(name=user.username, issuer_name='AIOE')
            else:
                if hasattr(user, p):
                    setattr(user, p, params[p])
        if need_update:
            await user.save()
        res = user.json
        res['mfa_uri'] = mfa_uri
        return response_ok(res)
    except (ValidationError, IntegrityError):
        raise CustomError(ErrorCode.ParameterError)


@user_bp.post("/del")
@requires_permission("user:a")
async def delelte_user(request):
    """
    删除用户（需要user:a权限）

    请求参数：
        username: 用户名
    """
    user = await Account.get(username=request.json.get("username"), deleted=False)
    if not user:
        raise CustomError(ErrorCode.AccountNotFound)
    user.deleted = True
    await user.save()
    return response_ok("用户删除成功")


@user_bp.post("/reset-mfa-key")
@requires_permission("user:a")
async def refresh_mfa_key(request):
    """
    重置MFA密钥（需要user:a权限）

    请求参数：
        username: 用户名
    """
    account = await Account.get(username=request.json.get('username'), deleted=False)
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
    给账号分配角色（需要user:a权限）

    请求参数：
        username: 用户名
        role_names: 角色名称，多个用英文逗号隔开
    """
    username = request.json.get('username')
    account = await Account.filter(username=username, deleted=False).first()
    if not account:
        raise CustomError(ErrorCode.AccountNotFound)

    roles = request.json.get('role_names', '')
    for r in roles.split(','):
        role = await Role.filter(name=r, deleted=False).get()
        if not role:
            raise CustomError(ErrorCode.RoleNotFound)

        await account.roles.add(role)
    return response_ok("success")
