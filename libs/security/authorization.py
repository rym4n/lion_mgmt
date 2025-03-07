# coding=utf-8

import functools

from sanic.log import logger
from sanic.request import Request
from tortoise.exceptions import DoesNotExist

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security.authentication import authenticate
from libs.security.utils import get_ip
from models import Role, Account, AuthenticationSession


async def check_permissions(request: Request, *required_permissions: str) -> AuthenticationSession:
    authentication_session = await authenticate(request)
    if authentication_session.anonymous:
        logger.warning(f"Client {get_ip(request)} attempted an unauthorized action anonymously.")
        raise CustomError(ErrorCode.Unauthorized)
    roles = await authentication_session.bearer.roles.filter(deleted=False).all()
    for role in roles:
        for role_permission in role.permissions:
            for required_permission in required_permissions:
                if check_wildcard(role_permission, required_permission):
                    return authentication_session
    logger.warning(
        f"Client {get_ip(request)} with account {authentication_session.bearer.id} attempted an unauthorized action."
    )
    raise CustomError(ErrorCode.Unauthorized)


def check_wildcard(wildcard: str, pattern: str):
    """
    权限校验匹配规则

    Args:
        wildcard (str): A wildcard string (e.g., "a:b:c").
        pattern (str): A wildcard pattern optional (`*`) or comma-separated values to match against (e.g., "a:b,c:*").

    Returns:
        is_match
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


async def check_roles(request: Request, *required_roles: str) -> AuthenticationSession:
    authentication_session = await authenticate(request)
    if authentication_session.anonymous:
        logger.warning(
            f"Client {get_ip(request)} attempted an unauthorized action anonymously."
        )
        raise CustomError(ErrorCode.Unauthorized)
    if set(required_roles) & {
        role.name
        for role in await authentication_session.bearer.roles.filter(
            deleted=False
        ).all()
    }:
        return authentication_session
    logger.warning(
        f"Client {get_ip(request)} with account {authentication_session.bearer.id} attempted an unauthorized action"
    )
    raise CustomError(ErrorCode.Unauthorized)


async def assign_role(name: str, account: Account, description: str = None, *permissions: str) -> Role:
    """
    给账号分配角色
    """
    try:
        role = await Role.filter(name=name).get()
    except DoesNotExist:
        role = await Role.create(name=name, description=description, permissions=permissions)
    await account.roles.add(role)
    return role


def requires_permission(*required_permissions: str):
    """
    权限校验装饰器
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
