# coding=utf-8

import functools
import re
import time

import jwt
from sanic import Request

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models import Account
from settings import SECRET, SESSION_ENCODING_ALGORITHM, AUTHENTICATION_SESSION_EXPIRATION


async def authenticate(request: Request):
    """
    客户端身份验证会话和账户验证。 如果客户端身份验证会话在身份验证过程中过期，自动续期token。
    """
    payload = token_decode(request)

    if payload.get('exp', 0) < int(time.time()):
        raise CustomError(ErrorCode.SessionExpired)

    if payload.get('mfa_enable') is True and payload.get('mfa_auth') is not True:
        raise CustomError(ErrorCode.SecondFactorRequired)

    if payload.get('ip') != request.ip:
        # 防止会话劫持
        raise CustomError(ErrorCode.SessionInvalid)

    request.ctx.session = payload


def requires_authentication(arg=None):
    """
    认证装饰器，一般不需要，已经通过中间件统一认证
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            await authenticate(request)
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator(arg) if callable(arg) else decorator


def token_encode(account_id, mfa_enable, token_id: str, **kwargs) -> str:
    """
    根据账号信息生成token
    """
    current = int(time.time())
    data = {
        "id": token_id,
        "sub": account_id,
        "iat": current,
        "exp": current + AUTHENTICATION_SESSION_EXPIRATION,
        "mfa_enable": mfa_enable
    }
    for key, value in kwargs.items():
        data[key] = value
    encoded_session = jwt.encode(data, SECRET, SESSION_ENCODING_ALGORITHM)
    return encoded_session


def refresh_token(old_data: dict, **kwargs) -> str:
    """
    刷新token
    """
    data = old_data.copy()
    for key, value in kwargs.items():
        data[key] = value
    token = jwt.encode(data, SECRET, SESSION_ENCODING_ALGORITHM)
    return token


def token_decode(request: Request) -> dict:
    """
    将token解码成数据字典
    """
    token = request.headers.get("aioe-auth")
    if not token:
        raise CustomError(ErrorCode.SessionInvalid)

    try:
        payload = jwt.decode(token, SECRET, SESSION_ENCODING_ALGORITHM)
        return payload
    except:
        raise CustomError(ErrorCode.SessionInvalid)


def validate_password(password: str) -> str:
    """
    密码强度校验，必须包含大小写字母、数字、特殊字符、长度不小于8
    """
    if not re.search(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[@#$%^&+=!]).{8,}$", password):
        raise CustomError(ErrorCode.WeakPassword)
    return password
