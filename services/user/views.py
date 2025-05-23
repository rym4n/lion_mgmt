# coding=utf-8
import time
import uuid
from datetime import datetime

import pyotp
from argon2.exceptions import VerificationError, InvalidHashError
from sanic import Blueprint
from tortoise.exceptions import IntegrityError, DoesNotExist
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authentication import validate_password, token_encode, refresh_token, validate_username
from libs.security.authorization import requires_permission
from libs.utils import password_hasher, ip_in_list, get_client_ip
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
        username: 账号
        password: 密码
        display_name：姓名
        phone: 可选，电话号码
        email: 可选，邮箱
        disabled: 可选，是否禁用
        mfa_enable: 可选，是否启用MFA
        mfa_key: 可选，MFA密钥
        role_names: 可选，角色名称列表，逗号分隔
        permit_ip: 可选，允许访问的IP列表，逗号分隔 192.168.1.1,192.168.1.2  如果为空则为全部ip
    """
    try:
        mfa_enable = bool(request.json.get('mfa_enable', False)) or False
        account = await Account.create(
            username=validate_username(request.json.get("username", '')),
            password=password_hasher.hash(validate_password(request.json.get("password") or '')),
            display_name=request.json.get("display_name"),
            email=request.json.get("email"),
            phone=request.json.get("phone"),
            disabled=request.json.get("disabled") or False,
            mfa_enable=mfa_enable,
            mfa_key=pyotp.random_base32(32) if mfa_enable else None,
            permit_ip=request.json.get("permit_ip", None),
        )
        # 添加角色
        if request.json.get("role_names"):
            roles = await Role.filter(name__in=request.json.get("role_names")).all()
            await account.roles.add(*roles)
        await account.fetch_related("roles")
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
        "deleted": False,
        "type": "user"
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


@user_bp.post("/new-token")
@requires_permission("user:c")
async def create_api_user(request):
    """
    创建API用户

    请求参数：
        - name: 必填，用户名
        - expiration_time: 必填，过期时间，单位：秒
        - role_names: 可选，角色名称列表，逗号分隔
        - description: 可选，描述
        - permit_ip: 可选，允许访问的IP列表，逗号分隔 192.168.1.1,192.168.1.2  如果为空则为全部ip
    """
    account = await Account.create(
        username=validate_username(request.json.get("name", '')),
        password=password_hasher.hash(str(uuid.uuid4())),
        type="api",
        display_name=request.json.get("description", ""),
        expiration=datetime.fromtimestamp(int(time.time()) + request.json.get("expiration_time", AUTHENTICATION_SESSION_EXPIRATION)),
        creator=request.ctx.session['account_info'].username,
        permit_ip=request.json.get("permit_ip", "all"),
    )
    # 添加角色
    if request.json.get("role_names"):
        roles = await Role.filter(name__in=request.json.get("role_names")).all()
        await account.roles.add(*roles)

    token_id = str(uuid.uuid4())
    token = token_encode(account.id, account.username, account.mfa_enable, token_id, request.json.get("expiration_time", AUTHENTICATION_SESSION_EXPIRATION), account.password, permit_ip=account.permit_ip)
    await account.fetch_related("roles")
    await request.ctx.cache_client.set(f"account_{account.id}", account, 7 * 24 * 3600)

    data_json = account.api_json
    data_json['token'] = account.id + ":" + token
    return response_ok(data_json)


@user_bp.post("/list-token")
@requires_permission("user:r")
async def list_api_users(request):
    """
    获取所有API用户信息

    请求参数：
        page: 可选，分页的页码，默认1
        size: 可选，分页的大小，默认10
        keyword: 可选，搜索时使用
    """
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 10))
    offset = (page - 1) * size
    limit = size
    # 分页查询ApiUserToken，返回总数和数据
    condition = Q(type="api") & Q(deleted=False)
    keyword = request.json.get("keyword")
    if keyword:
        condition = condition & Q(username__icontains=keyword) | Q(display_name=keyword)

    total = await Account.filter(condition).count()
    api_users = await Account.filter(condition).offset(offset).limit(limit).all().prefetch_related("roles")
    data = []
    for u in api_users:
        print(u.expiration)
        data.append(u.api_json)

    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": data
    })


@user_bp.post("/del-token")
@requires_permission("user:u")
async def update_api_user(request):
    """
    更新API用户信息（需要user:u权限）

    请求参数：
        id: 必填，API用户ID
    """
    api_user = await Account.get(id=request.json.get("id"), deleted=False)
    if not api_user:
        raise CustomError(ErrorCode.ApiUserNotFound)

    await request.ctx.cache_client.delete(f"account_{api_user.id}")

    api_user.deleted = True
    await api_user.save(update_fields=["deleted"])

    return response_ok("删除成功")


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
    if user_attempts is None:
        user_attempts = 0
    if user_attempts > MAX_CHALLENGE_ATTEMPTS:
        raise CustomError(ErrorCode.MaxChallenge)

    client_ip = get_client_ip(request)

    # 防止暴力破解/撞库
    ip_attempts = await request.ctx.cache_client.get(f"{client_ip}_login_attempts")
    if ip_attempts is None:
        ip_attempts = 0
    if ip_attempts > MAX_CHALLENGE_ATTEMPTS:
        raise CustomError(ErrorCode.MaxChallenge)

    try:
        account = await Account.get(username=username, deleted=False).prefetch_related('roles')
        if account.permit_ip and ip_in_list(client_ip, account.permit_ip.split(',')) is False:
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
        token = token_encode(account.id, account.username, account.mfa_enable, token_id, ip=client_ip)
        await request.ctx.cache_client.set(f"account_{account.id}", account, AUTHENTICATION_SESSION_EXPIRATION)
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
        # 如果开启了MFA，缓存记录MFA密钥，便于mfa认证
        if account.mfa_enable and account.mfa_key:
            await request.ctx.cache_client.set(f'mfa_key_{token_id}', account.mfa_key,
                                               AUTHENTICATION_SESSION_EXPIRATION)
            # 如果是开启mfa之后的第一次登录，在mfa还没绑定的情况下，返回mfa_uri，供前端渲染二维码
            if not account.mfa_bind:
                resp['mfa_uri'] = pyotp.totp.TOTP(account.mfa_key).provisioning_uri(name=account.username,
                                                                                    issuer_name='AIOE')

        return response_ok(resp)
    except DoesNotExist:
        raise CustomError(ErrorCode.CredentialsError)
    except (VerificationError, InvalidHashError):
        await request.ctx.cache_client.set(f"{username}_login_attempts", user_attempts + 1, 60 * 5)
        await request.ctx.cache_client.set(f"{client_ip}_login_attempts", ip_attempts + 1, 60 * 5)
        # await request.ctx.cache_client.expire(f"{username}_login_attempts", 60 * 5)
        # await request.ctx.cache_client.expire(f"{client_ip}_login_attempts", 60 * 5)
        raise CustomError(ErrorCode.CredentialsError)


@user_bp.post("/refresh-token")
async def refresh_session_token(request):
    """
    刷新token
    """
    resp = {
        "aioe-auth": refresh_token(request.ctx.session)
    }
    account = await request.ctx.cache_client.get(f"account_{request.ctx.session['sub']}")
    if not account:
        account = await Account.filter(id=request.ctx.session["sub"]).first().prefetch_related('roles')
    await request.ctx.cache_client.set(f"account_{request.ctx.session['sub']}", account, AUTHENTICATION_SESSION_EXPIRATION)
    return response_ok(resp)


@user_bp.post("/logout")
async def logout(request):
    """
    用户登出
    """
    # todo: oauth凭据销毁
    # token_info = await oauth_revoke(request, google_oauth)
    await request.ctx.cache_client.delete(f"account_{request.ctx.session['sub']}")
    return response_ok("登出成功")


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

    await Account.filter(id=request.ctx.session.get('sub')).update(mfa_bind=True)
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
        permit_ip: 可选，允许登录的IP地址，支持多个IP或CIDR格式（逗号分隔），例如 192.168.1.1,10.0.2.0/24
    """
    account = request.ctx.session['account_info']

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
        permit_ip: 可选，允许登录的IP地址，支持多个IP或CIDR格式（逗号分隔），例如 192.168.1.1,10.0.2.0/24
    """
    user = await Account.get(username=request.json.get("username"), deleted=False).prefetch_related('roles')
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
            if user.mfa_enable == params[p]:
                continue
            user.mfa_enable = params[p]
            user.mfa_key = pyotp.random_base32(32) if user.mfa_enable else None
            user.mfa_bind = False
            if user.mfa_key:
                # 关 -> 开
                mfa_uri = pyotp.totp.TOTP(user.mfa_key).provisioning_uri(name=user.username, issuer_name='AIOE')
        else:
            if hasattr(user, p):
                setattr(user, p, params[p])
    if need_update:
        await user.save()
    res = user.json
    if mfa_uri:
        res['mfa_uri'] = mfa_uri
    return response_ok(res)


@user_bp.post("/del")
@requires_permission("user:a")
async def delelte_user(request):
    """
    删除用户（需要user:a权限）

    请求参数：
        username: 用户名
    """
    await Account.filter(username=request.json.get("username")).update(deleted=True)
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
    account = await Account.get_or_none(username=username, deleted=False)
    if not account:
        raise CustomError(ErrorCode.AccountNotFound)

    role_names = request.json.get('role_names', '').split(',')
    if role_names in [[""], None]:
        await account.roles.clear()
        return response_ok("success")
    roles = await Role.filter(name__in=role_names, deleted=False).all()
    if len(roles) != len(role_names):
        raise CustomError(ErrorCode.RoleNotFound)

    await account.roles.clear()
    await account.roles.add(*roles)
    return response_ok("success")
