# coding=utf-8

import functools
import re
import time

import jwt
from sanic import Request
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.utils import get_client_ip, ip_in_list
from models import Account
from settings import SECRET, SESSION_ENCODING_ALGORITHM, AUTHENTICATION_SESSION_EXPIRATION


async def authenticate(request: Request):
    """
    客户端身份验证会话和账户验证
    """
    payload = await token_decode(request)
    validate_token(payload, request)
    account = await request.app.ctx.cache_client.get(f"account_{payload['sub']}")
    if not account:
        logger.warning("redis里已经没有account信息了")
        raise CustomError(ErrorCode.SessionInvalid)
    request.ctx.session['account_info'] = account


def validate_token(payload, request):
    if payload.get('exp', 0) < int(time.time()):
        raise CustomError(ErrorCode.SessionExpired)

    if not request.path.endswith('/user/mfa-auth') and payload.get('mfa_enable') is True and payload.get(
            'mfa_auth') is not True:
        raise CustomError(ErrorCode.SecondFactorRequired)

    client_ip = get_client_ip(request)

    if payload.get('ip'):
        if payload.get('ip') != client_ip:
            # 防止会话劫持
            raise CustomError(ErrorCode.SessionInvalid)
    else:
        payload['ip'] = client_ip

    # API访问时验证是否满足IP白名单
    if payload.get('permit_ip'):
        if ip_in_list(client_ip, payload['permit_ip'].split(',')) is False:
            raise CustomError(ErrorCode.IpForbidden)

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


def token_encode(account_id, username, display_name, mfa_enable, token_id: str,
                 expiration=AUTHENTICATION_SESSION_EXPIRATION, secret=SECRET, **kwargs) -> str:
    """
    根据账号信息生成token
    """
    current = int(time.time())
    data = {
        "id": token_id,
        "sub": account_id,
        "name": username,
        "display_name": display_name,
        "iat": current,
        "exp": current + expiration,
        "mfa_enable": mfa_enable
    }
    for key, value in kwargs.items():
        data[key] = value
    encoded_session = jwt.encode(data, secret, SESSION_ENCODING_ALGORITHM)
    return encoded_session


def refresh_token(old_data: dict, **kwargs) -> str:
    """
    刷新token
    """
    data = old_data.copy()
    for key, value in kwargs.items():
        data[key] = value
    current = int(time.time())
    data['iat'] = current
    data['exp'] = current + AUTHENTICATION_SESSION_EXPIRATION
    token = jwt.encode(data, SECRET, SESSION_ENCODING_ALGORITHM)
    return token


async def token_decode(request: Request) -> dict:
    """
    将token解码成数据字典
    """
    if request.cookies.get("aioe-auth") and ':' not in request.cookies.get("aioe-auth"):
        token = request.cookies.get("aioe-auth")
    elif request.headers.get("aioe-auth") and ':' in request.headers.get("aioe-auth"):
        token = request.headers.get("aioe-auth")
    else:
        raise CustomError(ErrorCode.SessionInvalid)

    try:
        if ":" in token:
            user_id = token.split(":")[0]
            token = token.split(":")[1]
            account = await request.app.ctx.cache_client.get(f"account_{user_id}")
            if not account:
                account = await Account.get(id=user_id, deleted=False).prefetch_related('roles')
                await request.app.ctx.cache_client.set(f"account_{account.id}", account, 7 * 24 * 3600)
            payload = jwt.decode(token, account.password, SESSION_ENCODING_ALGORITHM)
        else:
            payload = jwt.decode(token, SECRET, SESSION_ENCODING_ALGORITHM)
        return payload
    except Exception:
        raise CustomError(ErrorCode.SessionInvalid)


def validate_username(username: str) -> str:
    """
    用户名校验，只允许英文字母、数字和中文字符、长度不小于2
    """
    if not re.search(r"^[a-zA-Z0-9\u4e00-\u9fa5\.·]{2,}$", username):
        raise CustomError(ErrorCode.InvalidUsername)
    return username


def validate_password(password: str) -> str:
    """
    密码强度校验，必须包含大小写字母、数字、特殊字符、长度不小于8
    """
    if not re.search(r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[@#$%^&+=!]).{8,}$", password):
        raise CustomError(ErrorCode.WeakPassword)
    return password
