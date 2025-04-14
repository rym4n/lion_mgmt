# coding=utf-8

import functools

from sanic.log import logger
from sanic.request import Request

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.authentication import token_decode


async def check_permissions(request: Request, *required_permissions: str) -> bool:
    payload = token_decode(request)
    account = await request.ctx.cache_client.get(f"account_{payload['sub']}")
    if not account:
        logger.warning("redis里已经没有account信息了")
        raise CustomError(ErrorCode.SessionInvalid)
    request.ctx.session['account_info'] = account
    # roles = await account.roles.filter(deleted=False).all()
    for role in account.roles:
        for role_permission in role.permissions:
            for required_permission in required_permissions:
                if check_wildcard(role_permission, required_permission):
                    return True
    logger.warning(f"{request.client_ip} {payload['sub']} 尝试了一个未授权操作")
    raise CustomError(ErrorCode.Unauthorized)


def check_wildcard(wildcard: str, pattern: str):
    """
    权限校验匹配规则，wildcard 代表允许的权限，pattern 代表需要校验的权限

    例如：
        wildcard: role:* 代表允许执行role模块的所有操作
        pattern: role:create 代表需要执行role模块的create操作的权限
        True 代表满足权限要求

        wildcard: role:read
        pattern: role:create
        False
    """
    wildcard_parts = [set(part.split(",")) for part in wildcard.split(":")]
    pattern_parts = [set(part.split(",")) for part in pattern.split(":")]
    for i, pattern_part in enumerate(pattern_parts):
        if i >= len(wildcard_parts):
            return False
        wildcard_part = wildcard_parts[i]
        if "*" not in wildcard_part and not wildcard_part.issuperset(pattern_part):
            return False
    return all("*" in part for part in wildcard_parts[len(pattern_parts):])


async def check_roles(request: Request, *required_roles: str) -> bool:
    payload = token_decode(request)
    account = await request.ctx.cache_client.get(f"account_{payload['sub']}")
    if not account:
        logger.warning("redis里已经没有account信息了")
        raise CustomError(ErrorCode.SessionInvalid)
    request.ctx.session['account_info'] = account
    if set(required_roles) & {role.name for role in account.roles}:
        return True
    logger.warning(f"{request.client_ip} {payload['sub']} 尝试了一个未授权操作")
    raise CustomError(ErrorCode.Unauthorized)


def requires_permission(*required_permissions: str):
    """
    权限校验装饰器，定义权限需要明确，不能使用通配符。标识统一为：
        增 - c
        删 - d
        改 - u
        查 - r
        管理 - a
    请注意此装饰器只能判断垂直权限，不能判断横向权限
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            await check_permissions(request, *required_permissions)
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator


def requires_role(*required_roles: str):
    """
    角色检查装饰器
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            await check_roles(request, *required_roles)
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator
