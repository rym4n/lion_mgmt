import ast
import asyncio
import os
import uuid
from datetime import datetime
from urllib.parse import quote

import aiofile
import jwt
import orjson
import yaml
from aiocache import cached
from sanic import Blueprint
from sanic import json
from sanic import response
from sanic_ext import openapi
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs import utils
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import strip_timezone_to_seconds, get_from_s3, gen_run_id
from models import AuditLog, Config, Playbook, Role, PlaybookPermissions, PlaybookVersion
from services.event_ingestion.views import check_vector_config
from services.tools.views import tool_action_update
from services.workflow.views import statistics_data
from settings import SECRET, SESSION_ENCODING_ALGORITHM, BASE_DIR, VECTOR_CFG, AUTHENTICATION_SESSION_EXPIRATION

system_bp = Blueprint("system", url_prefix="/system")
_all__ = ["system_bp"]


@system_bp.post("/upload")
@openapi.definition(
    summary="保存系统定制配置",
    description="保存系统定制配置（如果 system_version 存在则更新，否则创建），需要系统配置新增和修改权限",
    body={
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "copyright": {"type": "string", "description": "版权"},
                    "system_name": {"type": "string", "description": "系统名"},
                    "system_version": {"type": "string", "description": "系统版本"},
                    "background": {"type": "string", "format": "binary", "description": "背景图"},
                    "webico": {"type": "string", "format": "binary", "description": "网页ico"},
                    "logo": {"type": "string", "format": "binary", "description": "logo"},
                },
            }
        }
    },
    response={"200": {"description": "保存成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer"}, "data": {"type": "string"}}}}}}},
)
@requires_permission("system:c")
async def upload_image(request):
    copyright_text = request.form.get("copyright")
    system_name = request.form.get("system_name")
    system_version = request.form.get("system_version")

    if not system_version:
        raise CustomError(ErrorCode.ParameterError)

    cfg = await Config.get_or_none(key="platform_config", module="system")
    if cfg:
        platform_config = orjson.loads(cfg.value)

        platform_config["Version"] = system_version
        platform_config["Content"] = copyright_text
        platform_config["Title"] = system_name
        await Config.filter(key="platform_config", module="system").update(value=orjson.dumps(platform_config).decode())
    else:
        default = {
            "Version": system_version or "3.0.0",
            "Title": system_name or "AIOE智能运营与自动化系统",
            "Content": copyright_text or "Copyright © 2025 虹云数据",
            "FixedHeader": True,
            "HiddenSideBar": False,
            "MultiTagsCache": False,
            "KeepAlive": True,
            "Layout": "horizontal",
            "Theme": os.environ.get("RY_MGMT_THEME") or "oceanBlue",
            "DarkMode": False,
            "OverallStyle": "default",
            "Grey": False,
            "Weak": False,
            "HideTabs": True,
            "HideFooter": False,
            "Stretch": False,
            "SidebarStatus": True,
            "EpThemeColor": "#2c7fe4",
            "ShowLogo": True,
            "ShowModel": "smart",
            "MenuArrowIconNoTransition": False,
            "CachingAsyncRoutes": False,
            "TooltipEffect": "default",
            "ResponsiveStorageNameSpace": "responsive-",
            "MenuSearchHistory": 6,
        }
        await Config.create(key="platform_config", module="system", value=orjson.dumps(default).decode())

    # 处理文件上传
    for field in ["background", "logo"]:
        file = request.files.get(field)
        filename = ""
        if file:
            original_name = file.name
            _, ext = os.path.splitext(original_name)
            if field == "background":
                filename = f"bg" + ext
            elif field == "logo":
                filename = f"logo" + ext
            if file and filename:
                file_url = await utils.upload_to_s3(file.body, "user", filename, "custom")
                await Config.update_or_create(key=field, module="system", defaults={"value": file_url})

    return response_ok("success")


@system_bp.get("/picture")
@openapi.definition(
    summary="获取自定义图片",
    description="获取自定义图片",
    parameter=[{"name": "type", "in": "query", "description": "图片类型: background, webico, logo", "required": True, "schema": {"type": "string"}}],
    response={"200": {"description": "获取成功", "content": {"image/*": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def get_bgpic(request):
    pic_type = request.args.get("type")
    if pic_type not in ("background", "webico", "logo"):
        raise CustomError(ErrorCode.ParameterError)
    cfg = await Config.get_or_none(key=pic_type, module="system")
    if cfg:
        return response_ok({"url": cfg.value})
    return response.raw(b"", content_type="image/png")


@system_bp.get("/platform")
@openapi.definition(
    summary="获取platform-config",
    description="获取平台配置信息",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "Version": {"type": "string"},
                            "Title": {"type": "string"},
                            "Content": {"type": "string"},
                            "FixedHeader": {"type": "boolean"},
                            "HiddenSideBar": {"type": "boolean"},
                            "MultiTagsCache": {"type": "boolean"},
                            "KeepAlive": {"type": "boolean"},
                            "Layout": {"type": "string"},
                            "Theme": {"type": "string"},
                            "DarkMode": {"type": "boolean"},
                            "OverallStyle": {"type": "string"},
                            "Grey": {"type": "boolean"},
                            "Weak": {"type": "boolean"},
                            "HideTabs": {"type": "boolean"},
                            "HideFooter": {"type": "boolean"},
                            "Stretch": {"type": "boolean"},
                            "SidebarStatus": {"type": "boolean"},
                            "EpThemeColor": {"type": "string"},
                            "ShowLogo": {"type": "boolean"},
                            "ShowModel": {"type": "string"},
                            "MenuArrowIconNoTransition": {"type": "boolean"},
                            "CachingAsyncRoutes": {"type": "boolean"},
                            "TooltipEffect": {"type": "string"},
                            "ResponsiveStorageNameSpace": {"type": "string"},
                            "MenuSearchHistory": {"type": "integer"},
                        },
                    }
                }
            },
        }
    },
)
async def get_platform_config(request):
    cfg = await Config.get_or_none(key="platform_config", module="system")
    default = {
        "Version": "V3.0",
        "Title": "AIOE智能运营与自动化系统",
        "Content": "Copyright © 2025 虹云数据",
        "FixedHeader": True,
        "HiddenSideBar": False,
        "MultiTagsCache": False,
        "KeepAlive": True,
        "Layout": "horizontal",
        "Theme": os.environ.get("RY_MGMT_THEME") or "oceanBlue",
        "DarkMode": False,
        "OverallStyle": "default",
        "Grey": False,
        "Weak": False,
        "HideTabs": True,
        "HideFooter": False,
        "Stretch": False,
        "SidebarStatus": True,
        "EpThemeColor": "#2c7fe4",
        "ShowLogo": True,
        "ShowModel": "smart",
        "MenuArrowIconNoTransition": False,
        "CachingAsyncRoutes": False,
        "TooltipEffect": "default",
        "ResponsiveStorageNameSpace": "responsive-",
        "MenuSearchHistory": 6,
    }

    if cfg:
        return json(orjson.loads(cfg.value), dumps=orjson.dumps)
    else:
        return json(default, dumps=orjson.dumps)


@system_bp.post("/status/all")
@openapi.definition(
    summary="获取全部系统状况",
    description="获取全部系统状况，需要系统配置查看权限",
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
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "host": {"type": "string"},
                                        "name": {"type": "string"},
                                        "status": {"type": "string"},
                                        "start_time": {"type": "string"},
                                        "check_time": {"type": "string"},
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("status:r")
@cached(timeout=120)
async def get_system_status(request):
    name_map = {
        "lion-mgmt": "控制台服务",
        "lion-web": "Web服务器",
        "lion-db": "数据库服务",
        "lion-kb": "数据库服务",
        "lion-redis": "缓存服务",
        "lion-ship": "ETL服务",
        "lion-job": "任务调度服务",
        "lion-worker": "执行引擎",
        "lion-kafka": "消息队列",
        "lion-ai": "AI服务",
        "lion-fs": "文件存储服务",
        "lion-vl": "日志存储服务",
        "lion-es": "日志存储服务",
    }
    # 遍历目录获取 status_ 开头的json文件
    data_dir = os.path.join(BASE_DIR, "data")
    status_files = [f for f in os.listdir(data_dir) if f.startswith("status_") and f.endswith(".json")]
    if not status_files:
        raise CustomError(ErrorCode.CheckStatusNotFound)
    result = []
    for file in status_files:
        ip = file.split("_")[1].strip(".json")
        file_path = os.path.join(data_dir, file)
        modify_time = datetime.strftime(datetime.fromtimestamp(os.path.getmtime(file_path)), "%Y-%m-%d %H:%M:%S")
        async with aiofile.AIOFile(file_path, "r", encoding="utf-8") as f:
            content = await f.read()
            data = orjson.loads(content)
            for item in data:
                item["host"] = ip
                item["name"] = name_map.get(item["name"], item["name"])
                item["start_time"] = strip_timezone_to_seconds(item["start_time"])
                item["check_time"] = modify_time
                result.append(item)

    return response_ok(result)


@system_bp.post("/oauth/status")
@openapi.definition(
    summary="获取SSO状态",
    description="获取SSO状态",
    response={
        "200": {
            "description": "获取成功",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer"}, "data": {"type": "object", "properties": {"use_oauth2": {"type": "boolean"}}}}}}},
        }
    },
)
async def get_oauth_status(request):
    if request.cookies.get("aioe-auth") and ":" not in request.cookies.get("aioe-auth"):
        try:
            token = request.cookies.get("aioe-auth")
            payload = jwt.decode(token, SECRET, SESSION_ENCODING_ALGORITHM)
            account = await request.app.ctx.cache_client.get(f"account_{payload['sub']}")
            if account:
                resp = account.json
                resp["use_oauth2"] = request.app.ctx.oauth_cfg.get("use_oauth2") == "true"
                return response_ok(resp)
        except Exception:
            pass

    return response_ok({"use_oauth2": request.app.ctx.oauth_cfg.get("use_oauth2") == "true"})


@system_bp.post("/oauth/get")
@openapi.definition(
    summary="获取SSO配置",
    description="获取SSO配置，需要sso:r权限",
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
                                    "use_oauth2": {"type": "string"},
                                    "auto_create": {"type": "string"},
                                    "client_id": {"type": "string"},
                                    "client_id_alias": {"type": "string"},
                                    "client_secret": {"type": "string"},
                                    "client_secret_alias": {"type": "string"},
                                    "authorize_uri": {"type": "string"},
                                    "token_uri": {"type": "string"},
                                    "token_path": {"type": "string"},
                                    "user_info_uri": {"type": "string"},
                                    "user_id_path": {"type": "string"},
                                    "display_name_path": {"type": "string"},
                                    "email_path": {"type": "string"},
                                    "phone_path": {"type": "string"},
                                    "scopes": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("sso:r")
async def get_oauth_config(request):
    records = await Config.filter(module="sso").values("key", "value")
    oauth_config = {c["key"]: c["value"] for c in records}
    secret = oauth_config.get("client_secret", "")
    return response_ok(
        {
            "use_oauth2": oauth_config.get("use_oauth2", "false"),
            "auto_create": oauth_config.get("auto_create", "false"),
            "client_id": oauth_config.get("client_id", ""),
            "client_id_alias": oauth_config.get("client_id_alias", ""),
            "client_secret": secret[:3] + "******" + secret[-3:] if secret else "",
            "client_secret_alias": oauth_config.get("client_secret_alias", ""),
            "authorize_uri": oauth_config.get("authorize_uri", ""),
            "token_uri": oauth_config.get("token_uri", ""),
            "token_path": oauth_config.get("token_path", ""),
            "user_info_uri": oauth_config.get("user_info_uri", ""),
            "user_id_path": oauth_config.get("user_id_path", ""),
            "display_name_path": oauth_config.get("display_name_path", ""),
            "email_path": oauth_config.get("email_path", ""),
            "phone_path": oauth_config.get("phone_path", ""),
            "scopes": oauth_config.get("scopes", ""),
        }
    )


@system_bp.post("/oauth/update")
@openapi.definition(
    summary="更新SSO配置",
    description="更新SSO配置，需要sso:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["use_oauth2", "auto_create", "client_id", "client_secret", "authorize_uri", "token_uri", "user_info_uri", "user_id_path", "token_path"],
                "properties": {
                    "use_oauth2": {"type": "string", "description": "true 表示启用oauth认证, false 表示禁用"},
                    "auto_create": {"type": "string", "description": "true 表示认证用户不存在的话自动新建, false 表示不新建"},
                    "client_id": {"type": "string", "description": "客户端ID"},
                    "client_id_alias": {"type": "string", "description": "客户端ID的名称，某些SSO可能叫app_id"},
                    "client_secret": {"type": "string", "description": "客户端密钥"},
                    "client_secret_alias": {"type": "string", "description": "客户端密钥的名称，某些SSO可能叫app_secret"},
                    "authorize_uri": {"type": "string", "description": "获取授权码的URI"},
                    "token_uri": {"type": "string", "description": "获取Token的URI"},
                    "token_path": {"type": "string", "description": "Token的取值路径"},
                    "user_info_uri": {"type": "string", "description": "获取用户信息的URI"},
                    "user_id_path": {"type": "string", "description": "用户唯一标识的取值路径"},
                    "display_name_path": {"type": "string", "description": "用户姓名的取值路径"},
                    "email_path": {"type": "string", "description": "用户邮箱的取值路径"},
                    "phone_path": {"type": "string", "description": "用户手机号的取值路径"},
                    "scopes_alias": {"type": "string", "description": "请求资源范围的名称"},
                    "scopes": {"type": "string", "description": "请求资源范围"},
                },
            }
        }
    },
    response={"200": {"description": "更新成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer"}, "data": {"type": "string"}}}}}}},
)
@requires_permission("sso:u")
async def update_oauth_config(request):
    params = request.json
    use_oauth2 = params.get("use_oauth2")
    if use_oauth2 not in ("true", "false"):
        raise CustomError(ErrorCode.ParameterError)

    if use_oauth2 == "false":
        old = await Config.get_or_none(key="use_oauth2")
        if old:
            old.value = "false"
            await old.save()
        else:
            await Config.create(key="use_oauth2", value="false", module="sso")
    else:
        required_fields = ["auto_create", "client_id", "client_secret", "authorize_uri", "token_uri", "user_info_uri", "user_id_path", "token_path"]
        optional_fields = ["display_name_path", "email_path", "phone_path", "scopes", "client_id_alias", "client_secret_alias", "scopes_alias"]
        # 校验必填字段是否为空或不存在
        for field in required_fields:
            if not params.get(field):
                raise CustomError(ErrorCode.ParameterError)

        # 收集所有要更新的键值对
        all_keys = required_fields + optional_fields
        updates = {key: params.get(key, "") for key in all_keys}
        updates["use_oauth2"] = "true"
        if "******" in params.get("client_secret", ""):
            updates.pop("client_secret")

        # 使用事务保证原子性
        async with in_transaction():  # 假设 ORM 支持异步事务
            # 批量获取现有配置
            existing_configs = await Config.filter(key__in=updates.keys()).all()
            existing_dict = {cfg.key: cfg for cfg in existing_configs}

            # 准备批量更新和创建的数据
            to_update = []
            to_create = []

            for key, value in updates.items():
                if key in existing_dict:
                    config = existing_dict[key]
                    config.value = value
                    to_update.append(config)
                else:
                    to_create.append(Config(key=key, value=value, module="sso"))

            # 批量更新
            if to_update:
                await Config.bulk_update(to_update, fields=["value"])

            # 批量创建
            if to_create:
                await Config.bulk_create(to_create)

    request.app.m.restart(zero_downtime=True, all_workers=True)
    return response_ok("success")


@system_bp.post("/ai/update")
@openapi.definition(
    summary="设置AI参数",
    description="设置AI参数，需要system:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "llm_host": {"type": "string", "description": "AI服务的Host（需带有协议，如 https://xxx.com）"},
                    "llm_api_key": {"type": "string", "description": 'AI服务的API Key（可选，留空则不更新，无密钥时传"EMPTY"）'},
                    "llm_model": {"type": "string", "description": "AI服务的模型名称（可选，留空则不更新）"},
                },
            }
        }
    },
    response={"200": {"description": "设置成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer"}, "data": {"type": "string"}}}}}}},
)
@requires_permission("system:u")
async def set_ai(request):
    params = request.json
    args = ["llm_host", "llm_api_key", "llm_model"]

    for a in args:
        if params.get(a):
            await Config.update_or_create(key=a, defaults={"value": params[a]}, module="ai")
    ai_cfg = {a: params[a] for a in args}
    await request.app.ctx.cache_client.set("ai_config", ai_cfg, 60 * 60 * 24)

    return response_ok("success")


@system_bp.post("/transforms-update")
@openapi.definition(
    summary="更新transforms模型",
    description="更新transforms模型",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"data": {"type": "array", "items": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}, "type": {"type": "string"}}}}},
            }
        }
    },
    response={"200": {"description": "更新成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer"}, "data": {"type": "string"}}}}}}},
)
async def transforms_update(request):
    for item in request.json.get("data"):
        await Config.update_or_create(key=item.get("key"), defaults={"value": item.get("value"), "type": item.get("type")}, module="transforms")

    return response_ok("添加成功")


@system_bp.post("/audit")
@openapi.definition(
    summary="查询审计日志",
    description="查询审计日志，需要audit:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "用户名"},
                    "start_time": {"type": "string", "description": "开始时间"},
                    "end_time": {"type": "string", "description": "结束时间"},
                    "ip": {"type": "string", "description": "ip"},
                    "page": {"type": "integer", "description": "页码"},
                    "size": {"type": "integer", "description": "每页条数"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "查询成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "total": {"type": "integer"},
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total_pages": {"type": "integer"},
                                    "data": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                                "username": {"type": "string"},
                                                "display_name": {"type": "string"},
                                                "client_ip": {"type": "string"},
                                                "result": {"type": "string"},
                                                "uri": {"type": "string"},
                                                "client_ua": {"type": "string"},
                                                "request_data": {"type": "object"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("audit:r")
async def search_audit(request):
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 15))
    offset = (page - 1) * size
    limit = size

    username = request.json.get("username")
    start_time = request.json.get("start_time")
    end_time = request.json.get("end_time")
    ip = request.json.get("ip")
    filters = {}
    if start_time:
        filters["ctime__gte"] = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    if end_time:
        filters["ctime__lte"] = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    if ip:
        filters["client_ip"] = ip
    if username:
        filters["username"] = username

    total = await AuditLog.filter(**filters).count()
    data = await AuditLog.filter(**filters).offset(offset).limit(limit)

    return response_ok({"total": total, "page": page, "size": size, "total_pages": (total + size - 1) // size, "data": [item.json for item in data]})


@system_bp.post("/outgoing")
@openapi.definition(
    summary="审计日志外发配置",
    description="审计日志外发配置，需要audit:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sink_type": {"type": "string", "description": "外发类型，可选kafka或syslog"},
                        "sink_address": {"type": "string", "description": "外发地址，包括端口，多个地址用,分隔"},
                        "sink_protocol": {"type": "string", "description": "外发协议，可选tcp或udp"},
                        "kafka_topic": {"type": "string", "description": "kafka主题名称，可选"},
                        "sasl_mechanism": {"type": "string", "description": "SASL机制，可选"},
                        "sasl_username": {"type": "string", "description": "SASL用户名，可选"},
                        "sasl_password": {"type": "string", "description": "SASL密码，可选"},
                    },
                },
            }
        }
    },
    response={"200": {"description": "配置成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer"}, "data": {"type": "string"}}}}}}},
)
@requires_permission("audit:r")
async def outgoing(request):
    data = {}
    yaml_file = os.path.join(VECTOR_CFG, "aioe_audit.yaml")
    with open(yaml_file, "r", encoding="utf-8") as f:
        old_data = yaml.safe_load(f)  # 转换成 Python 对象

    data["sources"] = old_data["sources"]
    data.setdefault("sinks", {})["aioe_pg_audit"] = old_data["sinks"]["aioe_pg_audit"]

    # 校验重复项
    address_sum = []
    # 避免重名
    m = 0
    json_data = request.json
    for item in json_data:
        sink_type = item.get("sink_type")
        sink_address = item.get("sink_address")
        if not sink_address and not sink_type:
            raise CustomError(ErrorCode.ParameterError)

        sink_address = sink_address.split(",")
        if len(sink_address) > 1 and sink_type == "syslog":
            raise CustomError(ErrorCode.MultipleSyslogAddressNotSupported)

        for a in sink_address:
            if int(a.split(":")[-1]) < 1 or int(a.split(":")[-1]) > 65535:
                raise CustomError(ErrorCode.InvalidPortError)

            if a not in address_sum:
                address_sum.append(a)
            else:
                raise CustomError(ErrorCode.OutboundAddressCanNotBeRepeated)

        if sink_type == "kafka":
            topic = item.get("kafka_topic")
            mechanism = item.get("sasl_mechanism")
            username = item.get("sasl_username")
            password = item.get("sasl_password")
            if not topic:
                raise CustomError(ErrorCode.ParameterError)
            data["sinks"]["kafka_sinks_" + str(m)] = {"type": "kafka", "inputs": ["aioe_kafka_audit"], "bootstrap_servers": ",".join(sink_address), "topic": topic, "encoding": {"codec": "json"}}
            if mechanism:
                data["sinks"]["kafka_sinks"]["sasl"] = {"enabled": True, "mechanism": mechanism, "username": username, "password": password}
        if sink_type == "syslog":
            mode = item.get("sink_protocol")
            if not mode:
                raise CustomError(ErrorCode.ParameterError)
            for address in sink_address:
                data["sinks"]["syslog_sinks_" + str(m)] = {"type": "socket", "inputs": ["aioe_kafka_audit"], "address": address, "mode": mode, "encoding": {"codec": "json"}}
        m += 1

    class QuotedString(str):
        pass

    def quoted_presenter(dumper, data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

    yaml.add_representer(QuotedString, quoted_presenter)

    data["sinks"]["aioe_pg_audit"]["endpoint"] = QuotedString(data["sinks"]["aioe_pg_audit"]["endpoint"])

    yaml_str = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

    result = check_vector_config(data, vector_config_yaml=yaml_str)

    if not result:
        raise CustomError(ErrorCode.OutboundFileConfigurationFailed)

    async with aiofile.AIOFile(yaml_file, mode="w", encoding="utf-8") as file:
        await file.write(yaml_str)

    await Config.update_or_create(key="outgoing_audit_log", module="audit_log", defaults={"value": str(json_data)})

    return response_ok("保存成功")


@system_bp.post("/outgoing-config")
@openapi.definition(
    summary="获取审计日志外发配置",
    description="获取审计日志外发配置，需要audit:r权限",
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
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "sink_type": {"type": "string"},
                                        "sink_address": {"type": "string"},
                                        "sink_protocol": {"type": "string"},
                                        "kafka_topic": {"type": "string"},
                                        "sasl_mechanism": {"type": "string"},
                                        "sasl_username": {"type": "string"},
                                        "sasl_password": {"type": "string"},
                                    },
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("audit:r")
async def get_outgoing_config(request):
    config = await Config.get_or_none(key="outgoing_audit_log", module="audit_log")
    if not config:
        return response_ok([])

    python_list = ast.literal_eval(config.value)
    return response_ok(python_list)


@system_bp.post("/up-mt")
@openapi.definition(
    summary="更新维护模式状态",
    description="更新维护模式状态，需要audit:r权限",
    body={"application/json": {"schema": {"type": "object", "required": ["mt_status"], "properties": {"mt_status": {"type": "integer", "description": "1表示开启维护模式，0表示关闭维护模式"}}}}},
    response={"200": {"description": "更新成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer"}, "data": {"type": "string"}}}}}}},
)
@requires_permission("audit:r")
async def update_maintenance_mode_status(request):
    if request.json.get("mt_status") is None:
        raise CustomError(ErrorCode.ParameterError)

    mt_status = int(request.json.get("mt_status"))
    if request.ctx.session["account_info"].username == "admin":
        if mt_status == 1:
            await request.app.ctx.cache_client.set(request.ctx.session["id"], True, AUTHENTICATION_SESSION_EXPIRATION)
        else:
            await request.app.ctx.cache_client.set(request.ctx.session["id"], False, AUTHENTICATION_SESSION_EXPIRATION)
    else:
        raise CustomError(ErrorCode.MaintenanceModeError)
    return response_ok("更新成功")


@system_bp.post("/up-file")
@openapi.definition(
    summary="上传文件",
    description="上传文件，需要audit:r权限",
    body={"multipart/form-data": {"schema": {"type": "object", "properties": {"file": {"type": "string", "format": "binary", "description": "字节流文件"}}}}},
    response={
        "200": {
            "description": "上传成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "files": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {"filename": {"type": "string"}, "file_id": {"type": "string"}, "size": {"type": "integer"}, "result": {"type": "string"}},
                                        },
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("audit:r")
async def update_file(request):
    if not request.files or "file" not in request.files:
        raise CustomError(ErrorCode.ParameterError)

    files = request.files.getlist("file")
    saved_files = []

    for f in files:
        filename = f.name or f.filename
        ext = filename.split(".")[-1]
        file_id = str(uuid.uuid4())
        s3_filename = file_id + "." + ext

        # 异步写入文件
        await utils.upload_to_s3(f.body, "user", s3_filename, "common_files")

        file_info = {"filename": filename, "file_id": s3_filename, "size": len(f.body), "result": "success"}
        saved_files.append(file_info)

    return response_ok({"files": saved_files})


@system_bp.get("/down-file/<file_id>")
@openapi.definition(
    summary="下载文件",
    description="下载文件",
    parameter=[
        {"name": "file_id", "in": "path", "description": "文件名（上传时返回的 file_id）", "required": True, "schema": {"type": "string"}},
        {"name": "download_name", "in": "query", "description": "下载文件名", "required": False, "schema": {"type": "string"}},
    ],
    response={"200": {"description": "下载成功", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def download_file(request, file_id):
    if not file_id:
        raise CustomError(ErrorCode.ParameterError)
    safe_name = os.path.basename(file_id)

    content = await get_from_s3("user", file_id, "common_files")

    if content:
        tmp_path = os.path.join("/tmp", file_id)
        os.makedirs(os.path.dirname(tmp_path), exist_ok=True)

        async with aiofile.AIOFile(tmp_path, "wb") as afp:
            await afp.write(content)

        download_name = request.args.get("download_name") or safe_name

        if "." not in download_name and "." in safe_name:
            ext = safe_name.split(".")[-1]
            download_name += "." + ext

        # 对中文文件名进行编码（RFC 5987）
        quoted_filename = quote(download_name)
        headers = {"Content-Disposition": f"attachment; filename=\"{download_name}\";filename*=UTF-8''{quoted_filename}", "Content-Type": "application/octet-stream; charset=utf-8"}

        try:
            return await response.file(tmp_path, mime_type="application/octet-stream", headers=headers)
        finally:
            await asyncio.to_thread(os.remove, tmp_path)
    else:
        return response_ok("文件不存在")


@system_bp.post("/new")
@openapi.definition(
    summary="初始化剧本",
    description="初始化剧本",
    body={"application/json": {"schema": {"type": "object", "required": ["flow_list"], "properties": {"flow_list": {"type": "array", "items": {"type": "object"}, "description": "剧本列表"}}}}},
    response={
        "200": {
            "description": "创建成功",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer"}, "data": {"type": "string"}, "message": {"type": "string"}}}}},
        }
    },
)
@requires_permission("workflow:c")
async def create_workflow(request):
    data = request.json
    if not data.get("flow_list"):
        raise CustomError(ErrorCode.ParameterError)

    for flow_data in data.get("flow_list"):
        # 判断name是否已经存在，如果存在的话要抛出异常
        if await Playbook.filter(name=flow_data.get("name"), deleted=False).exists():
            raise CustomError(ErrorCode.PlaybookNameExists)

        flow_json_list = flow_data.get("flow_json_list") or []
        creator = request.ctx.session["account_info"]
        admin = await Role.get(id="111111")
        async with in_transaction():
            playbook = await Playbook.create(
                id=str(flow_data.get("flow_id")),
                name=flow_data.get("name"),
                remark="",
                creator=creator,
                internal=True,
            )
            await PlaybookPermissions.create(pb=playbook, users=creator, roles=None, permissions=["r", "u", "d", "e", "p"])
            await PlaybookPermissions.create(pb=playbook, users=None, roles=admin, permissions=["r", "u", "d", "e", "p"])

            for flow_json in flow_json_list:
                pv = await PlaybookVersion.create(playbook=playbook, version=gen_run_id(), status=0, change_desc="", flow_json=flow_json, creator=creator, updator=creator, flow_timeout=300)

                action_ids = await statistics_data(flow_json)
                await tool_action_update({pv.id: action_ids})

    return response_ok("success")
