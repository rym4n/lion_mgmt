# coding=utf-8
import traceback

import pyotp
from argon2.exceptions import VerificationError, InvalidHashError
from sanic import Blueprint
from sanic.log import logger
from tortoise.exceptions import ValidationError, IntegrityError

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authentication import validate_password
from libs.security.utils import password_hasher, get_ip
from models import Account, AuthenticationSession
from settings import AUTHENTICATION_SESSION_EXPIRATION, MAX_CHALLENGE_ATTEMPTS

user_bp = Blueprint('user', url_prefix='user/v1')
_all__ = ["user_bp"]


@user_bp.post("/new")
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
        logger.info(f"{get_ip(request)} 添加账号 {account.username}.")
        res = account.json
        if mfa_enable:
            res['mfa_uri'] = pyotp.totp.TOTP(account.mfa_key).provisioning_uri(name=account.username, issuer_name='AIOE')
        return response_ok(res)
    except ValidationError as e:
        logger.error(f"{e} | {traceback.format_exc()}")
        raise CustomError(ErrorCode.ValidationError)
    except IntegrityError:
        raise CustomError(ErrorCode.AccountExists)


@user_bp.post("/login")
async def login(request):
    """
    用户登录

    请求参数：
        username: 用户名
        password: 密码
    """
    try:
        username = request.json.get("username")
        password = request.json.get("password")
        if not username or not password:
            raise CustomError(ErrorCode.CredentialsError)
        account = await Account.get_via_username(username)
        # 防止暴力破解
        key = f"{account.id}_login_attempts"
        attempts = await request.ctx.cache_client.get(key)
        if attempts and int(attempts) > MAX_CHALLENGE_ATTEMPTS:
            raise CustomError(ErrorCode.MaxChallenge)
        # 验证密码
        password_hasher.verify(account.password, password)
        if password_hasher.check_needs_rehash(account.password):
            account.password = password_hasher.hash(password)
            await account.save(update_fields=["password"])
        account.validate()
        authentication_session = await AuthenticationSession.new(request, account, requires_second_factor=account.mfa_enable)
        logger.info(f"{get_ip(request)} 成功登录并生成会话 {authentication_session.id}.")
        # 如果开启了MFA，记录MFA密钥
        authentication_session.requires_second_factor = account.mfa_enable
        await request.app.ctx.cache_client.set(authentication_session.id, account.mfa_key, AUTHENTICATION_SESSION_EXPIRATION)
        response = response_ok(authentication_session.bearer.json)
        authentication_session.encode(response)
        return response
    except (VerificationError, InvalidHashError):
        logger.warning(f"{get_ip(request)} 登录失败 {account.id}.")
        await request.ctx.cache_client.increment(key, 1)
        await request.ctx.cache_client.expire(key, 60 * 5)
        raise CustomError(ErrorCode.CredentialsError)


@user_bp.post("/logout")
async def logout(request):
    """用户登出"""
    authentication_session = await AuthenticationSession.decode(request)
    if not authentication_session.active:
        raise CustomError(ErrorCode.SessionDeactivated)
    authentication_session.active = False
    await authentication_session.save(update_fields=["active"])
    logger.info(f"{get_ip(request)} 成功注销会话 {authentication_session.id}.")
    # todo: oauth凭据销毁
    # token_info = await oauth_revoke(request, google_oauth)
    return response_ok(authentication_session.json)


@user_bp.post("/mfa-auth")
async def mfa_auth(request):
    """
    用户二次验证
    :param code MFA验证码
    """
    authentication_session = await AuthenticationSession.decode(request)
    if not authentication_session.requires_second_factor:
        raise CustomError(ErrorCode.NONEEDMFA)
    mfa_key = await request.app.ctx.cache_client.get(authentication_session.id)
    totp = pyotp.TOTP(mfa_key)
    code = request.json.get('code')
    if not code or not mfa_key or not totp.verify(code):
        raise CustomError(ErrorCode.MFAError)

    # await authentication_session.save(update_fields=["requires_second_factor"])
    logger.info(f"来自 {get_ip(request)} 的会话 {authentication_session.id} 成功通过MFA验证")

    response = response_ok(authentication_session.bearer.json)
    return response


@user_bp.post("/edit")
async def edit_user(request):
    """
    修改用户信息
    """
    authentication_session = await AuthenticationSession.decode(request)
    logger.info(authentication_session.account.username)
    return response_ok()


@user_bp.post("/del")
async def delelte_user(request):
    """
    删除用户
    :param userid: 用户名
    :return:
    """
    try:
        user = await Account.filter(id=request.json.get("userid"), deleted=False).first()
        if not user:
            raise CustomError(ErrorCode.AccountNotFound)
        user.deleted = True
        await user.save()
        return response_ok("用户删除成功")
    except IntegrityError as e:
        raise CustomError(ErrorCode.ServerError)
