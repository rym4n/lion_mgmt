# coding=utf-8

from sanic import Blueprint
from sanic_ext import openapi
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.security.authentication import SESSION_HIJACKING_PROTECTION_CACHE_KEY
from models import Config
from settings import SECURE_CONFIG_DEFAULT

secure_bp = Blueprint("secure", url_prefix="/secure")


@secure_bp.post("/detail")
@openapi.definition(
    summary="获取安全配置",
    description="获取安全配置信息，需要secure:r权限",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "password_length_min": {"type": "integer"},
                                    "password_length_max": {"type": "integer"},
                                    "password_include_lowercase": {"type": "boolean"},
                                    "password_include_uppercase": {"type": "boolean"},
                                    "password_include_digit": {"type": "boolean"},
                                    "password_include_special": {"type": "boolean"},
                                    "password_exclude_continuous_length": {"type": "integer"},
                                    "password_exclude_keyboard_order": {"type": "boolean"},
                                    "password_exclude_letter_order": {"type": "boolean"},
                                    "password_exclude_digit_order": {"type": "boolean"},
                                    "password_exclude_special_order": {"type": "boolean"},
                                    "password_exclude_repeat": {"type": "boolean"},
                                    "password_validity_period": {"type": "string"},
                                    "password_max_retries": {"type": "integer"},
                                    "account_lock_duration": {"type": "integer"},
                                    "account_auto_freeze_days": {"type": "integer"},
                                    "account_auto_freeze_enabled": {"type": "boolean"},
                                    "force_change_password_first_login": {"type": "boolean"},
                                    "mfa_enabled": {"type": "boolean"},
                                    "session_hijacking_protection": {"type": "boolean"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("secure:r")
async def get_secure_config(request):
    configs = await Config.filter(module="secure").all()
    config_dict = {c.key: c.value for c in configs}

    # 合并配置，处理类型转换
    result = {}
    for key, default_value in SECURE_CONFIG_DEFAULT.items():
        value = config_dict.get(key)
        if value is None:
            result[key] = default_value
        else:
            # 根据默认值的类型进行转换
            if isinstance(default_value, bool):
                result[key] = value.lower() == "true"
            elif isinstance(default_value, int):
                try:
                    result[key] = int(value)
                except ValueError:
                    result[key] = default_value
            else:
                result[key] = value

    return response_ok(result)


@secure_bp.post("/update")
@openapi.definition(
    summary="更新安全配置",
    description="更新安全配置信息，需要secure:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "password_length_min": {"type": "integer"},
                    "password_length_max": {"type": "integer"},
                    "password_include_lowercase": {"type": "boolean"},
                    "password_include_uppercase": {"type": "boolean"},
                    "password_include_digit": {"type": "boolean"},
                    "password_include_special": {"type": "boolean"},
                    "password_exclude_continuous_length": {"type": "integer"},
                    "password_exclude_keyboard_order": {"type": "boolean"},
                    "password_exclude_letter_order": {"type": "boolean"},
                    "password_exclude_digit_order": {"type": "boolean"},
                    "password_exclude_special_order": {"type": "boolean"},
                    "password_exclude_repeat": {"type": "boolean"},
                    "password_validity_period": {"type": "string"},
                    "password_max_retries": {"type": "integer"},
                    "account_lock_duration": {"type": "integer"},
                    "account_auto_freeze_days": {"type": "integer"},
                    "account_auto_freeze_enabled": {"type": "boolean"},
                    "force_change_password_first_login": {"type": "boolean"},
                    "mfa_enabled": {"type": "boolean"},
                    "session_hijacking_protection": {"type": "boolean"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "更新成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("secure:u")
async def update_secure_config(request):
    params = request.json
    if not params:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 允许的配置项
    allowed_keys = {
        "password_length_min",
        "password_length_max",
        "password_include_lowercase",
        "password_include_uppercase",
        "password_include_digit",
        "password_include_special",
        "password_exclude_continuous_length",
        "password_exclude_keyboard_order",
        "password_exclude_letter_order",
        "password_exclude_digit_order",
        "password_exclude_special_order",
        "password_exclude_repeat",
        "password_validity_period",
        "password_max_retries",
        "account_lock_duration",
        "account_auto_freeze_days",
        "account_auto_freeze_enabled",
        "force_change_password_first_login",
        "mfa_enabled",
        "session_hijacking_protection",
    }

    async with in_transaction():
        for key, value in params.items():
            if key in allowed_keys:
                # 统一转换为字符串存储
                str_value = str(value).lower() if isinstance(value, bool) else str(value)
                await Config.update_or_create(key=key, defaults={"value": str_value, "module": "secure"})
                if key == "session_hijacking_protection":
                    await request.app.ctx.cache_client.set(
                        SESSION_HIJACKING_PROTECTION_CACHE_KEY,
                        value is True or str(value).lower() == "true",
                    )

    return response_ok("更新成功")
