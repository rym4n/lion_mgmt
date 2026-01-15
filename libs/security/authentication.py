# coding=utf-8

import functools
import re
import time
import traceback

import jwt
from sanic import Request
from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.utils import get_client_ip, ip_in_list
from models import Account, Config, Tenant
from settings import AUTHENTICATION_SESSION_EXPIRATION, SECRET, SECURE_CONFIG_DEFAULT, SESSION_ENCODING_ALGORITHM


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
    request.ctx.session["account_info"] = account


def validate_token(payload, request):
    if payload.get("exp", 0) < int(time.time()):
        raise CustomError(ErrorCode.SessionExpired)

    if not request.path.endswith("/user/mfa-auth") and payload.get("mfa_enable") is True and payload.get("mfa_auth") is not True:
        raise CustomError(ErrorCode.SecondFactorRequired)

    client_ip = get_client_ip(request)

    if payload.get("ip"):
        if payload.get("ip") != client_ip:
            # 防止会话劫持
            raise CustomError(ErrorCode.SessionInvalid)
    else:
        payload["ip"] = client_ip

    # API访问时验证是否满足IP白名单
    if payload.get("permit_ip"):
        if ip_in_list(client_ip, payload["permit_ip"].split(",")) is False:
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


def token_encode(
    account_id,
    username,
    display_name,
    mfa_enable,
    tenant_id,
    token_id: str,
    expiration=AUTHENTICATION_SESSION_EXPIRATION,
    secret=SECRET,
    **kwargs,
) -> str:
    """
    根据账号信息生成token
    """
    current = int(time.time())
    data = {
        "id": token_id,
        "sub": account_id,
        "tenant_id": tenant_id,
        "name": username,
        "display_name": display_name,
        "iat": current,
        "exp": current + expiration,
        "mfa_enable": mfa_enable,
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
    data["iat"] = current
    data["exp"] = current + AUTHENTICATION_SESSION_EXPIRATION
    data.pop("account_info")
    token = jwt.encode(data, SECRET, SESSION_ENCODING_ALGORITHM)
    return token


async def token_decode(request: Request) -> dict:
    """
    将token解码成数据字典
    """
    if request.cookies.get("aioe-auth") and ":" not in request.cookies.get("aioe-auth"):
        token = request.cookies.get("aioe-auth")
    elif request.headers.get("aioe-auth") and ":" in request.headers.get("aioe-auth"):
        token = request.headers.get("aioe-auth")
    else:
        raise CustomError(ErrorCode.SessionInvalid)

    try:
        if ":" in token:
            user_id = token.split(":")[0]
            token = token.split(":")[1]
            account = await request.app.ctx.cache_client.get(f"account_{user_id}")
            if not account:
                account = await Account.get(id=user_id, deleted=False).prefetch_related("roles")
                await request.app.ctx.cache_client.set(f"account_{account.id}", account, 7 * 24 * 3600)
            payload = jwt.decode(token, account.password, SESSION_ENCODING_ALGORITHM)
            if request.headers.get("tenant_name"):
                tenant = await Tenant.get_or_none(name=request.headers.get("tenant_name"))
                if tenant:
                    payload["tenant_id"] = tenant.id
        else:
            payload = jwt.decode(token, SECRET, SESSION_ENCODING_ALGORITHM)
        return payload
    except Exception as e:
        logger.warning(f"{token} | {e} | {traceback.format_exc()}")
        raise CustomError(ErrorCode.SessionInvalid)


def validate_username(username: str) -> str:
    """
    用户名校验，只允许英文字母、数字和中文字符、长度不小于2
    """
    if not re.search(r"^[a-zA-Z0-9\u4e00-\u9fa5\.·]{2,}$", username):
        raise CustomError(ErrorCode.InvalidUsername)
    return username


async def get_secure_config_dict() -> dict:
    """
    获取安全配置，与数据库合并
    """
    configs = await Config.filter(module="secure").all()
    config_dict = {c.key: c.value for c in configs}

    result = {}
    for key, default_value in SECURE_CONFIG_DEFAULT.items():
        value = config_dict.get(key)
        if value is None:
            result[key] = default_value
        else:
            if isinstance(default_value, bool):
                result[key] = value.lower() == "true"
            elif isinstance(default_value, int):
                try:
                    result[key] = int(value)
                except ValueError:
                    result[key] = default_value
            else:
                result[key] = value
    return result


def has_consecutive_both(s: str, n: int = 5) -> bool:
    if n <= 1:
        return True
    inc = dec = 1
    for i in range(1, len(s)):
        d = ord(s[i]) - ord(s[i - 1])
        if d == 1:
            inc += 1
            dec = 1
        elif d == -1:
            dec += 1
            inc = 1
        else:
            inc = dec = 1
        if inc >= n or dec >= n:
            return True
    return False


def has_keyboard_sequence_plus(s: str, min_len: int = 4, include_reverse: bool = True):
    seqs = [
        "1234567890",
        "qwertyuiop",
        "asdfghjkl",
        "zxcvbnm",
        "1qaz",
        "2wsx",
        "3edc",
        "4rfv",
        "5tgb",
        "6yhn",
        "7ujm",
        "qaz",
        "wsx",
        "edc",
        "rfv",
        "tgb",
        "yhn",
        "ujm",
    ]
    if include_reverse:
        seqs = seqs + [x[::-1] for x in seqs]
    s = s.casefold()
    for i in range(0, len(s) - min_len + 1):
        chunk = s[i : i + min_len]
        for seq in seqs:
            if chunk in seq:
                return True
    return False


async def validate_password(password: str) -> str:
    """
    密码强度校验，基于安全配置
    """
    config = await get_secure_config_dict()

    # 1. 长度校验
    if len(password) < config["password_length_min"] or len(password) > config["password_length_max"]:
        raise CustomError(ErrorCode.PasswordLengthError)

    # 2. 包含字符类型校验
    if config["password_include_lowercase"] and not re.search(r"[a-z]", password):
        raise CustomError(ErrorCode.PasswordMissingLowercase)
    if config["password_include_uppercase"] and not re.search(r"[A-Z]", password):
        raise CustomError(ErrorCode.PasswordMissingUppercase)
    if config["password_include_digit"] and not re.search(r"\d", password):
        raise CustomError(ErrorCode.PasswordMissingDigit)
    if config["password_include_special"] and not re.search(r"[@#$%^&+=!]", password):
        raise CustomError(ErrorCode.PasswordMissingSpecial)

    # 3. 连续字符长度校验
    if config["password_exclude_continuous_length"] > 0:
        if has_consecutive_both(password, config["password_exclude_continuous_length"]):
            raise CustomError(ErrorCode.PasswordContinuousChar)

    # 4. 键盘序校验
    if config["password_exclude_keyboard_order"]:
        has_seq = has_keyboard_sequence_plus(password)
        if has_seq:
            raise CustomError(ErrorCode.PasswordKeyboardOrder)

    # 5. 字符重复校验
    if config["password_exclude_repeat"]:
        # 检查是否有连续重复字符，例如 aaa
        if re.search(r"(.)\1\1", password):
            raise CustomError(ErrorCode.PasswordRepeatedChar)

    return password
