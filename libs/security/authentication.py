# coding=utf-8

import functools
import re

from argon2.exceptions import VerificationError, InvalidHashError
from sanic.log import logger
from sanic.request import Request
from tortoise.exceptions import ValidationError, IntegrityError

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.utils import get_ip, password_hasher
from models import Account, AuthenticationSession, TwoStepSession


async def register(request: Request, disabled: bool = False) -> Account:
    """
    新建账号
    """
    try:
        account = await Account.create(
            email=request.json.get("email").lower(),
            username=request.json.get("username"),
            password=password_hasher.hash(validate_password(request.json.get("password"))),
            phone=request.json.get("phone"),
            disabled=disabled,
        )
        logger.info(f"Client {get_ip(request)} has registered account {account.id}.")
        return account
    except ValidationError as e:
        raise CustomError(ErrorCode.CredentialsError)
    except IntegrityError as e:
        raise CustomError(ErrorCode.AccountExists)


async def login(request: Request, account: Account = None, require_second_factor: bool = False, password: str = None, ) -> AuthenticationSession:
    if not account:
        account, password = await Account.get_via_header(request)
    elif not password:
        raise CustomError(ErrorCode.CredentialsError)
    try:
        password_hasher.verify(account.password, password)
        if password_hasher.check_needs_rehash(account.password):
            account.password = password_hasher.hash(password)
            await account.save(update_fields=["password"])
        account.validate()
        authentication_session = await AuthenticationSession.new(request, account, requires_second_factor=require_second_factor)
        logger.info(f"Client {get_ip(request)} has logged in with authentication session {authentication_session.id}.")
        return authentication_session
    except (VerificationError, InvalidHashError):
        logger.warning(f"Client {get_ip(request)} has failed to log into account {account.id}.")
        raise CustomError(ErrorCode.CredentialsError)


async def logout(request: Request) -> AuthenticationSession:
    authentication_session = await AuthenticationSession.decode(request)
    if not authentication_session.active:
        raise CustomError(ErrorCode.SessionDeactivated)
    authentication_session.active = False
    await authentication_session.save(update_fields=["active"])
    logger.info(
        f"Client {get_ip(request)} has logged out with authentication session {authentication_session.id}."
    )
    return authentication_session


async def fulfill_second_factor(request: Request) -> AuthenticationSession:
    """
    完成客户端身份验证会话的二次验证
    """
    authentication_session = await AuthenticationSession.decode(request)
    if not authentication_session.requires_second_factor:
        raise CustomError(ErrorCode.MFAError)
    two_step_session = await TwoStepSession.decode(request)
    two_step_session.validate()
    await two_step_session.check_code(request.json.get("code"))
    authentication_session.requires_second_factor = False
    await authentication_session.save(update_fields=["requires_second_factor"])
    logger.info(f"Client {get_ip(request)} has fulfilled authentication session {authentication_session.id} second factor.")
    return authentication_session


async def authenticate(request: Request) -> AuthenticationSession:
    """客户端身份验证会话和账户验证。 如果客户端身份验证会话在身份验证过程中过期，新/刷新的会话将自动返回。"""
    authentication_session = await AuthenticationSession.decode(request)
    try:
        authentication_session.validate()
        if not authentication_session.anonymous:
            authentication_session.bearer.validate()
    except CustomError(ErrorCode.SessionExpired):
        authentication_session = await authentication_session.refresh(request)
        authentication_session.is_refresh = True
        request.ctx.session = authentication_session
    return authentication_session


def requires_authentication(arg=None):
    """
    认证装饰器
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            await authenticate(request)
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator(arg) if callable(arg) else decorator


def validate_password(password: str) -> str:
    if not re.search(r"^(?=.*[A-Z])(?=.*\d)(?=.*[@#$%^&+=!]).*$", password):
        raise CustomError(ErrorCode.WeakPassword)
    return password
