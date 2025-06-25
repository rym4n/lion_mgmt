# coding=utf-8

import functools
import hashlib
import os

from sanic.log import logger
from sanic.request import Request

from core.error_code import ErrorCode
from libs.error_code.enum import ErrorData
from libs.error_code.exception import CustomError
from libs.security.authentication import token_decode
from libs.utils import verify_license
from settings import LICENSE_DIR

MODEL_DICT = {
    "role": "角色管理",
    "user": "账号管理",
    "event": "事件列表",
    "ingestion": "事件接入",
    "var": "全局变量",
    "approve": "人工任务",
    "tool": "应用市场",
    "workflow": "剧本管理",
    "system": "系统设置",
    "scene": "场景管理",
    "timed": "定时任务",
    "lic": "产品许可",
    "warroom": "作战室",
    "dashboard": "仪表盘",
    "audit": "审计日志"
}


async def check_permissions(request: Request, *required_permissions: str) -> bool:
    payload = await token_decode(request)
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
    model = MODEL_DICT[request.name.split(".")[1]]
    raise CustomError(ErrorData(4004, f"缺少{model}模块的操作权限"))


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
    payload = await token_decode(request)
    account = await request.ctx.cache_client.get(f"account_{payload['sub']}")
    if not account:
        logger.warning("redis里已经没有account信息了")
        raise CustomError(ErrorCode.SessionInvalid)
    request.ctx.session['account_info'] = account
    if set(required_roles) & {role.name for role in account.roles}:
        return True
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


def load_license():
    for root, dirs, files in os.walk(LICENSE_DIR):
        for file in files:
            filename, ext = os.path.splitext(file)
            full_path = os.path.join(root, file)
            with open(full_path, "r") as f:
                data = f.read()
                if filename == hashlib.md5(f"{data}".encode()).hexdigest():
                    try:
                        lic = verify_license(data.encode())
                        return lic
                    except Exception as e:
                        logger.error(f"License无效: {e}")
    return None
