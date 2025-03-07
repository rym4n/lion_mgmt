# coding=utf-8

import functools
import re

from sanic.request import Request

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models import AuthenticationSession


async def authenticate(request: Request) -> AuthenticationSession:
    """客户端身份验证会话和账户验证。 如果客户端身份验证会话在身份验证过程中过期，新刷新的会话将自动返回。"""
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
    if not re.search(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[@#$%^&+=!]).{8,}$", password):
        raise CustomError(ErrorCode.WeakPassword)
    return password
