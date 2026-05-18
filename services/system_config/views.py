# coding=utf-8

import ast
import asyncio
import io
import os
import uuid
import zipfile
from datetime import datetime, timezone
from urllib.parse import quote

import aiofile
import jwt
import orjson
import pytz
import yaml
from sanic import Blueprint, json, response
from sanic.log import logger
from sanic_ext import openapi
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_fail, response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import load_license, requires_permission
from libs.utils import get_from_s3, query_vlog, strip_timezone_to_seconds, upload_to_s3
from models import Config, Playbook, PlaybookPermissions, PlaybookVersion, Role, Tool, ToolResources
from services.event_ingestion.views import check_vector_config
from services.tools.views import upload_tool
from services.workflow.views import statistics_data, tool_action_update
from settings import AUTHENTICATION_SESSION_EXPIRATION, SECRET, SESSION_ENCODING_ALGORITHM, TZ, VECTOR_CFG

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
                    "background": {
                        "type": "string",
                        "format": "binary",
                        "description": "背景图",
                    },
                    "webico": {
                        "type": "string",
                        "format": "binary",
                        "description": "网页ico",
                    },
                    "logo": {
                        "type": "string",
                        "format": "binary",
                        "description": "logo",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "保存成功",
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
@requires_permission("system:c")
async def upload_image(request):
    copyright_text = request.form.get("copyright")
    system_name = request.form.get("system_name")
    system_version = request.form.get("system_version")

    if not system_version:
        raise CustomError(ErrorCode.PARAM_ERROR)

    cfg = await Config.get_or_none(key="platform_config", module="system")
    if cfg:
        platform_config = cfg.json_value or {}

        platform_config["Version"] = system_version
        platform_config["Content"] = copyright_text
        platform_config["Title"] = system_name
        await Config.filter(key="platform_config", module="system").update(value=orjson.dumps(platform_config).decode())
    else:
        default = {
            "Version": system_version or "3.0",
            "Title": system_name or "AIOE智能运营与自动化系统",
            "Content": copyright_text or f" © {datetime.now().year}",
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
    uploaded_files = []
    for field in ["background", "logo"]:
        file = request.files.get(field)
        filename = ""
        if file:
            original_name = file.name
            _, ext = os.path.splitext(original_name)
            if field == "background":
                filename = "bg" + ext
            elif field == "logo":
                filename = "logo" + ext
            if file and filename:
                file_url = await upload_to_s3(file.body, "user", filename, "custom")
                await Config.update_or_create(key=field, module="system", defaults={"value": file_url})
                uploaded_files.append({"field": field, "url": file_url})

    return response_ok({"uploaded_files": uploaded_files})


@system_bp.get("/picture")
@openapi.definition(
    summary="获取自定义图片",
    description="获取自定义图片",
    parameter=[
        {
            "name": "type",
            "in": "query",
            "description": "图片类型: background, webico, logo",
            "required": True,
            "schema": {"type": "string"},
        }
    ],
    response={
        "200": {
            "description": "获取成功",
            "content": {"image/*": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def get_bgpic(request):
    pic_type = request.args.get("type")
    if pic_type not in ("background", "webico", "logo"):
        raise CustomError(ErrorCode.PARAM_ERROR)
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
        "Content": " © 2026",
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
        return json(cfg.json_value or default, dumps=orjson.dumps)
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
                                        "cpu_percent": {"type": "string"},
                                        "mem_percent": {"type": "string"},
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
async def get_system_status(request):
    # 从VictoriaLog查询最新的健康检查数据
    # 查询最近5分钟的数据，确保能覆盖所有主机
    query = 'log_type:"health_check" | sort(_time desc)'
    data_res = await query_vlog(query, project_id=0, limit=100)

    if not data_res:
        return response_ok([])

    logger.info(f"health check data count: {len(data_res)}")
    # 按主机分组，获取每个主机的最新一条记录
    host_latest = {}
    for item in data_res:
        log_ip = item.get("log_ip")
        if not log_ip:
            continue
        if log_ip not in host_latest:
            host_latest[log_ip] = item

    result = []
    for log_ip, log_data in host_latest.items():
        data = log_data.get("data")
        ctime = log_data.get("ctime", "")

        # 尝试从 _msg 中解析，因为 VictoriaLogs 可能不会自动展开 JSON 对象
        if not data and "_msg" in log_data:
            try:
                msg_data = orjson.loads(log_data["_msg"])
                if isinstance(msg_data, dict):
                    data = msg_data.get("data", {})
                    ctime = msg_data.get("ctime", ctime)
            except Exception:
                pass

        # 如果还是没有 data，尝试从展开的字段中重建（处理 Kafka 源的数据）
        if not data:
            data = {}
            # 查找所有 data.xxx.yyy 格式的字段
            for key, value in log_data.items():
                if key.startswith("data."):
                    parts = key.split(".", 2)  # 分割为 ["data", "service_name", "field_name"]
                    if len(parts) == 3:
                        service_name = parts[1]
                        field_name = parts[2]
                        if service_name not in data:
                            data[service_name] = {}
                        data[service_name][field_name] = value

        # 如果没有 ctime，尝试使用 timestamp（Kafka 源的数据）
        if not ctime:
            ctime = log_data.get("timestamp", "")

        # 如果 data 为空，跳过这个主机
        if not data:
            logger.warning(f"No service data found for host {log_ip}")
            continue

        # 处理时间格式
        check_time = ""
        if ctime:
            try:
                # 解析时间字符串（格式：2026-01-11T00:24:01+0800）
                if "+" in ctime or ctime.endswith("Z"):
                    dt = datetime.fromisoformat(ctime.replace("Z", "+00:00"))
                    dt_local = dt.astimezone(TZ)
                    check_time = dt_local.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    check_time = ctime
            except Exception:
                check_time = ctime

        # 将data中的每个服务展开为一条记录
        for service_name, service_info in data.items():
            if not isinstance(service_info, dict):
                continue

            status = service_info.get("run_status", "")
            cpu_percent = service_info.get("cpu_percent", "")
            mem_percent = service_info.get("mem_percent", "")
            start_time_str = service_info.get("start_time", "")
            health = service_info.get("health", "")

            start_time = ""
            if start_time_str:
                start_time = strip_timezone_to_seconds(start_time_str)

            result.append(
                {
                    "host": log_ip,
                    "name": service_name,
                    "run_status": status,
                    "health": health,
                    "cpu_percent": cpu_percent,
                    "mem_percent": mem_percent,
                    "start_time": start_time,
                    "check_time": check_time,
                }
            )

    return response_ok(result)


@system_bp.post("/status/history")
@openapi.definition(
    summary="查询健康检查历史数据",
    description="查询指定主机和服务的健康检查历史数据，需要系统状态查看权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["host", "service_name", "start_time", "end_time"],
                "properties": {
                    "host": {"type": "string", "description": "主机IP地址"},
                    "service_name": {"type": "string", "description": "服务名称（可选），如lion-mgmt、lion-web等"},
                    "start_time": {
                        "type": "string",
                        "description": "开始时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "结束时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
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
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "host": {"type": "string"},
                                        "name": {"type": "string"},
                                        "run_status": {"type": "string"},
                                        "health_status": {"type": "string"},
                                        "start_time": {"type": "string"},
                                        "check_time": {"type": "string"},
                                        "cpu_percent": {"type": "string"},
                                        "mem_usage": {"type": "string"},
                                        "mem_percent": {"type": "string"},
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
async def get_system_status_history(request):
    params = request.json
    host = params.get("host")
    service_name = params.get("service_name")
    start_time = params.get("start_time")
    end_time = params.get("end_time")

    if not host or not service_name or not start_time or not end_time:
        raise CustomError(ErrorCode.PARAM_ERROR)

    start_time_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    end_time_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    start_time_iso = start_time_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_time_iso = end_time_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 构建查询条件
    query = f'log_type:"health_check" AND log_ip:"{host}"'
    data_query = f"{query} | sort(_time desc)"

    # 从VictoriaLog查询指定时间范围的健康检查数据
    data_res = await query_vlog(data_query, project_id=0, start=start_time_iso, end=end_time_iso, limit=1500)

    if not data_res:
        return response_ok([])

    result = []
    for log_data in data_res:
        log_ip = log_data.get("log_ip", host)
        data = log_data.get("data")
        ctime = log_data.get("ctime", "")

        # 尝试从 _msg 中解析，因为 VictoriaLogs 可能不会自动展开 JSON 对象
        if not data and "_msg" in log_data:
            try:
                msg_data = orjson.loads(log_data["_msg"])
                if isinstance(msg_data, dict):
                    data = msg_data.get("data", {})
                    ctime = msg_data.get("ctime", ctime)
            except Exception:
                pass

        if not data:
            data = {}

        # 处理时间格式
        check_time = ""
        if ctime:
            try:
                # 解析时间字符串（格式：2026-01-11T00:24:01+0800）
                if "+" in ctime or ctime.endswith("Z"):
                    dt = datetime.fromisoformat(ctime.replace("Z", "+00:00"))
                    dt_local = dt.astimezone(TZ)
                    check_time = dt_local.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    check_time = ctime
            except Exception:
                check_time = ctime

        # 将data中的每个服务展开为一条记录
        for svc_name, service_info in data.items():
            if not isinstance(service_info, dict):
                continue

            # 如果指定了service_name，只处理匹配的服务
            if service_name and svc_name != service_name:
                continue

            status = service_info.get("run_status", "")
            start_time_str = service_info.get("start_time", "")
            health = service_info.get("health", "")
            cpu_percent = service_info.get("cpu_percent", "")
            mem_usage = service_info.get("mem_usage", "")
            mem_percent = service_info.get("mem_percent", "")

            start_time_formatted = ""
            if start_time_str:
                start_time_formatted = strip_timezone_to_seconds(start_time_str)

            result.append(
                {
                    "host": log_ip,
                    "name": svc_name,
                    "run_status": status,
                    "health": health,
                    "start_time": start_time_formatted,
                    "check_time": check_time,
                    "cpu_percent": cpu_percent,
                    "mem_usage": mem_usage,
                    "mem_percent": mem_percent,
                }
            )

    return response_ok(result)


@system_bp.post("/oauth/status")
@openapi.definition(
    summary="获取SSO状态",
    description="获取SSO状态",
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
                                "properties": {"use_oauth2": {"type": "boolean"}},
                            },
                        },
                    }
                }
            },
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
                resp = account.to_json(request.ctx.session.get("tenant_id"))
                resp["use_oauth2"] = request.app.ctx.oauth_cfg.get("use_oauth2") == "true"
                resp["tenant_id"] = request.ctx.session.get("tenant_id")
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
                                    "default_roles": {"type": "string"},
                                    "default_tenants": {"type": "string"},
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
            "default_roles": oauth_config.get("default_roles", ""),
            "default_tenants": oauth_config.get("default_tenants", ""),
            "scopes": oauth_config.get("scopes", ""),
        }
    )


@system_bp.post("/jwt/get")
@openapi.definition(
    summary="获取JWT单点配置",
    description="获取JWT单点配置，需要sso:r权限",
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
                                    "jwt_enabled": {"type": "string"},
                                    "jwt_algorithm": {"type": "string"},
                                    "jwt_secret": {"type": "string"},
                                    "jwt_user_path": {"type": "string"},
                                    "jwt_auto_create": {"type": "string"},
                                    "jwt_default_roles": {"type": "string"},
                                    "jwt_default_tenants": {"type": "string"},
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
async def get_jwt_config(request):
    records = await Config.filter(module="jwt").values("key", "value")
    cfg = {c["key"]: c["value"] for c in records}
    secret = cfg.get("jwt_secret", "")
    return response_ok(
        {
            "jwt_enabled": cfg.get("jwt_enabled", "false"),
            "jwt_algorithm": cfg.get("jwt_algorithm", "HS256"),
            "jwt_secret": secret[:3] + "******" + secret[-3:] if secret else "",
            "jwt_user_path": cfg.get("jwt_user_path", ""),
            "jwt_auto_create": cfg.get("jwt_auto_create", "false"),
            "jwt_default_roles": cfg.get("jwt_default_roles", ""),
            "jwt_default_tenants": cfg.get("jwt_default_tenants", ""),
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
                "required": [
                    "use_oauth2",
                    "auto_create",
                    "client_id",
                    "client_secret",
                    "authorize_uri",
                    "token_uri",
                    "user_info_uri",
                    "user_id_path",
                    "token_path",
                    "default_roles",
                ],
                "properties": {
                    "use_oauth2": {
                        "type": "string",
                        "description": "true 表示启用oauth认证, false 表示禁用",
                    },
                    "auto_create": {
                        "type": "string",
                        "description": "true 表示认证用户不存在的话自动新建, false 表示不新建",
                    },
                    "client_id": {"type": "string", "description": "客户端ID"},
                    "client_id_alias": {
                        "type": "string",
                        "description": "客户端ID的名称，某些SSO可能叫app_id",
                    },
                    "client_secret": {"type": "string", "description": "客户端密钥"},
                    "client_secret_alias": {
                        "type": "string",
                        "description": "客户端密钥的名称，某些SSO可能叫app_secret",
                    },
                    "authorize_uri": {
                        "type": "string",
                        "description": "获取授权码的URI",
                    },
                    "token_uri": {"type": "string", "description": "获取Token的URI"},
                    "token_path": {"type": "string", "description": "Token的取值路径"},
                    "user_info_uri": {
                        "type": "string",
                        "description": "获取用户信息的URI",
                    },
                    "user_id_path": {
                        "type": "string",
                        "description": "用户唯一标识的取值路径",
                    },
                    "display_name_path": {
                        "type": "string",
                        "description": "用户姓名的取值路径",
                    },
                    "email_path": {
                        "type": "string",
                        "description": "用户邮箱的取值路径",
                    },
                    "phone_path": {
                        "type": "string",
                        "description": "用户手机号的取值路径",
                    },
                    "default_roles": {
                        "type": "string",
                        "description": "新用户默认角色ID，多个用英文逗号分隔",
                    },
                    "default_tenants": {
                        "type": "string",
                        "description": "新用户默认租户ID，多个用英文逗号分隔",
                    },
                    "scopes_alias": {
                        "type": "string",
                        "description": "请求资源范围的名称",
                    },
                    "scopes": {"type": "string", "description": "请求资源范围"},
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
@requires_permission("sso:u")
async def update_oauth_config(request):
    params = request.json
    use_oauth2 = params.get("use_oauth2")
    if use_oauth2 not in ("true", "false"):
        raise CustomError(ErrorCode.PARAM_ERROR)

    if use_oauth2 == "false":
        old = await Config.get_or_none(key="use_oauth2")
        if old:
            old.value = "false"
            await old.save()
        else:
            await Config.create(key="use_oauth2", value="false", module="sso")
    else:
        required_fields = [
            "auto_create",
            "client_id",
            "client_secret",
            "authorize_uri",
            "token_uri",
            "user_info_uri",
            "user_id_path",
            "token_path",
        ]
        optional_fields = [
            "display_name_path",
            "email_path",
            "phone_path",
            "default_tenants",
            "default_roles",
            "scopes",
            "client_id_alias",
            "client_secret_alias",
            "scopes_alias",
        ]
        # 校验必填字段是否为空或不存在
        for field in required_fields:
            if not params.get(field):
                raise CustomError(ErrorCode.PARAM_ERROR)

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


@system_bp.post("/jwt/update")
@openapi.definition(
    summary="更新JWT单点配置",
    description="更新JWT单点配置，需要sso:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["jwt_enabled"],
                "properties": {
                    "jwt_enabled": {
                        "type": "string",
                        "description": "true 表示启用JWT单点, false 表示禁用",
                    },
                    "jwt_algorithm": {"type": "string", "description": "HS256 或 RS256"},
                    "jwt_secret": {"type": "string", "description": "校验密钥或公钥"},
                    "jwt_user_path": {"type": "string", "description": "提取用户ID的JSONPath"},
                    "jwt_auto_create": {
                        "type": "string",
                        "description": "true 表示认证用户不存在自动新建",
                    },
                    "jwt_default_roles": {
                        "type": "string",
                        "description": "新用户默认角色ID，英文逗号分隔",
                    },
                    "jwt_default_tenants": {
                        "type": "string",
                        "description": "新用户默认租户ID，英文逗号分隔",
                    },
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
                        "properties": {"code": {"type": "integer"}, "data": {"type": "string"}},
                    }
                }
            },
        }
    },
)
@requires_permission("sso:u")
async def update_jwt_config(request):
    params = request.json
    enabled = params.get("jwt_enabled")
    if enabled not in ("true", "false"):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if enabled == "false":
        old = await Config.get_or_none(key="jwt_enabled")
        if old:
            old.value = "false"
            await old.save()
        else:
            await Config.create(key="jwt_enabled", value="false", module="jwt")
    else:
        required_fields = ["jwt_algorithm", "jwt_secret", "jwt_user_path"]
        optional_fields = ["jwt_auto_create", "jwt_default_roles", "jwt_default_tenants"]
        for field in required_fields:
            if not params.get(field):
                raise CustomError(ErrorCode.PARAM_ERROR)
        updates = {key: params.get(key, "") for key in required_fields + optional_fields}
        updates["jwt_enabled"] = "true"
        if "******" in params.get("jwt_secret", ""):
            updates.pop("jwt_secret")
        async with in_transaction():
            existing_configs = await Config.filter(key__in=updates.keys()).all()
            existing_dict = {cfg.key: cfg for cfg in existing_configs}
            to_update = []
            to_create = []
            for key, value in updates.items():
                if key in existing_dict:
                    config = existing_dict[key]
                    config.value = value
                    to_update.append(config)
                else:
                    to_create.append(Config(key=key, value=value, module="jwt"))
            if to_update:
                await Config.bulk_update(to_update, fields=["value"])
            if to_create:
                await Config.bulk_create(to_create)
    request.app.m.restart(zero_downtime=True, all_workers=True)
    return response_ok("success")


@system_bp.post("/transforms-update")
@openapi.definition(
    summary="更新transforms模型",
    description="更新transforms模型",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string"},
                                "value": {"type": "string"},
                                "type": {"type": "string"},
                            },
                        },
                    }
                },
            }
        }
    },
    response={
        "200": {
            "description": "添加成功",
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
async def transforms_update(request):
    for item in request.json.get("data"):
        await Config.update_or_create(
            key=item.get("key"),
            defaults={"value": item.get("value"), "type": item.get("type"), "shared": True},
            module="transforms",
        )

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
                    "start_time": {
                        "type": "string",
                        "description": "开始时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "结束时间（UTC格式：2025-01-01T00:00:00Z）",
                    },
                    "ip": {"type": "string", "description": "ip"},
                    "action": {"type": "string", "description": "操作"},
                    "uri": {"type": "string", "description": "请求URI"},
                    "client_ua": {"type": "string", "description": "客户端User-Agent"},
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
                                                "username": {"type": "string"},
                                                "display_name": {"type": "string"},
                                                "client_ip": {"type": "string"},
                                                "action": {"type": "string"},
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
    page = max(1, int(request.json.get("page", 1)))
    size = max(1, min(int(request.json.get("size", 15)), 100))
    offset = (page - 1) * size

    if offset >= 1000:
        return response_fail(ErrorCode.OUT_OF_PAGE)

    username = request.json.get("username")
    start_time = request.json.get("start_time")
    end_time = request.json.get("end_time")

    start_iso = None
    end_iso = None

    if start_time:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        start_iso = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if end_time:
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        end_iso = end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ip = request.json.get("ip")
    action = request.json.get("action")
    uri = request.json.get("uri")
    client_ua = request.json.get("client_ua")

    query = 'topic:"lion_audit"'
    if username:
        query += f' AND username:"{username}"'
    if ip:
        query += f' AND client_ip:"{ip}"'
    if action:
        query += f' AND action:"{action}"'
    if uri:
        query += f' AND uri:"{uri}"'
    if client_ua:
        query += f' AND client_ua:"{client_ua}"'

    # 获取总数
    count_query = f"{query} | stats count()"
    count_task = query_vlog(count_query, project_id=0, start=start_iso, end=end_iso)

    # 获取数据
    data_query = f"{query} | sort(_time desc)"
    # 由于VictoriaLogs不支持offset，最多查询1000条数据
    data_task = query_vlog(data_query, project_id=0, start=start_iso, end=end_iso, limit=1000)

    count_res, data_res = await asyncio.gather(count_task, data_task)

    total = 0
    if count_res:
        total = int(count_res[0].get("count(*)", 0))

    data = []
    if data_res:
        # 内存分页
        page_data = data_res[offset : offset + size]
        for item in page_data:
            # 格式化时间，VictoriaLogs返回的_time通常是RFC3339格式，这里需要处理
            utc_str = item.get("_time")
            utc_time = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)

            data.append(
                {
                    "id": item.get("id", ""),
                    "ctime": utc_time.astimezone(TZ),
                    "username": item.get("username", ""),
                    "display_name": item.get("display_name", ""),
                    "client_ip": item.get("client_ip", ""),
                    "action": item.get("action") or "访问",
                    "result": item.get("result", ""),
                    "uri": item.get("uri", ""),
                    "client_ua": item.get("client_ua", ""),
                    "request_data": item.get("request_data", {}),
                }
            )

    return response_ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
            "data": data,
        }
    )


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
                        "sink_type": {
                            "type": "string",
                            "description": "外发类型，可选kafka或syslog",
                        },
                        "sink_address": {
                            "type": "string",
                            "description": "外发地址，包括端口，多个地址用,分隔",
                        },
                        "sink_protocol": {
                            "type": "string",
                            "description": "外发协议，可选tcp或udp",
                        },
                        "kafka_topic": {
                            "type": "string",
                            "description": "kafka主题名称，可选",
                        },
                        "sasl_mechanism": {
                            "type": "string",
                            "description": "SASL机制，可选",
                        },
                        "sasl_username": {
                            "type": "string",
                            "description": "SASL用户名，可选",
                        },
                        "sasl_password": {
                            "type": "string",
                            "description": "SASL密码，可选",
                        },
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "配置成功",
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
@requires_permission("audit:u")
async def outgoing(request):
    yaml_file = os.path.join(VECTOR_CFG, "aioe_audit.yaml")
    async with aiofile.AIOFile(yaml_file, "r", encoding="utf-8") as f:
        content = await f.read()
        old_data = yaml.safe_load(content)

    data = {"sources": old_data["sources"], "transforms": old_data["transforms"], "sinks": {}}
    data["sinks"]["aioe_default_audit"] = old_data["sinks"]["aioe_default_audit"]
    json_data = request.json
    seen_addresses = set()

    for i, item in enumerate(json_data):
        sink_type = item.get("sink_type")
        raw_address = item.get("sink_address")

        if not raw_address and not sink_type:
            raise CustomError(ErrorCode.PARAM_ERROR)

        addresses = raw_address.split(",")
        if len(addresses) > 1 and sink_type == "syslog":
            raise CustomError(ErrorCode.MULTIPLE_SYSLOG_ADDRESS_NOT_SUPPORTED)

        for addr in addresses:
            try:
                port = int(addr.split(":")[-1])
                if port > 65535 or port < 1:
                    raise CustomError(ErrorCode.INVALID_PORT_ERROR)
            except (ValueError, IndexError):
                raise CustomError(ErrorCode.INVALID_PORT_ERROR)

            if addr in seen_addresses:
                raise CustomError(ErrorCode.OUTBOUND_ADDRESS_CAN_NOT_BE_REPEATED)
            seen_addresses.add(addr)

        sink_key_suffix = str(i)
        common_config = {"inputs": old_data["sinks"]["aioe_default_audit"]["inputs"], "encoding": {"codec": "json"}}

        if sink_type == "kafka":
            topic = item.get("kafka_topic")
            if not topic:
                raise CustomError(ErrorCode.PARAM_ERROR)

            sink_config = {
                "type": "kafka",
                "bootstrap_servers": raw_address,
                "topic": topic,
                **common_config,
            }

            if mechanism := item.get("sasl_mechanism"):
                sink_config["sasl"] = {
                    "enabled": True,
                    "mechanism": mechanism,
                    "username": item.get("sasl_username"),
                    "password": item.get("sasl_password"),
                }
            data["sinks"][f"kafka_sinks_{sink_key_suffix}"] = sink_config

        elif sink_type == "syslog":
            mode = item.get("sink_protocol")
            if not mode:
                raise CustomError(ErrorCode.PARAM_ERROR)

            data["sinks"][f"syslog_sinks_{sink_key_suffix}"] = {
                "type": "socket",
                "address": addresses[0],
                "mode": mode,
                **common_config,
            }

    yaml_str = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

    if not check_vector_config(data, vector_config_yaml=yaml_str):
        raise CustomError(ErrorCode.OUTBOUND_FILE_CONFIGURATION_FAILED)

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
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["mt_status"],
                "properties": {
                    "mt_status": {
                        "type": "integer",
                        "description": "1表示开启维护模式，0表示关闭维护模式",
                    }
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
@requires_permission("audit:r")
async def update_maintenance_mode_status(request):
    if request.json.get("mt_status") is None:
        raise CustomError(ErrorCode.PARAM_ERROR)

    mt_status = int(request.json.get("mt_status"))
    if request.ctx.session["account_info"].username == "admin":
        if mt_status == 1:
            await request.app.ctx.cache_client.set(request.ctx.session["id"], True, AUTHENTICATION_SESSION_EXPIRATION)
        else:
            await request.app.ctx.cache_client.set(request.ctx.session["id"], False, AUTHENTICATION_SESSION_EXPIRATION)
    else:
        raise CustomError(ErrorCode.MAINTENANCE_MODE_ERROR)
    return response_ok("更新成功")


@system_bp.post("/up-file")
@openapi.definition(
    summary="上传文件",
    description="上传文件",
    body={
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "字节流文件",
                    }
                },
            }
        }
    },
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
                                            "properties": {
                                                "filename": {"type": "string"},
                                                "file_id": {"type": "string"},
                                                "size": {"type": "integer"},
                                                "result": {"type": "string"},
                                            },
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
async def update_file(request):
    if not request.files or "file" not in request.files:
        raise CustomError(ErrorCode.PARAM_ERROR)

    files = request.files.getlist("file")
    saved_files = []

    for f in files:
        filename = f.name or f.filename
        ext = filename.split(".")[-1]
        file_id = str(uuid.uuid4())
        s3_filename = file_id + "." + ext

        # 异步写入文件
        await upload_to_s3(f.body, "user", s3_filename, "common_files")

        file_info = {
            "filename": filename,
            "file_id": s3_filename,
            "size": len(f.body),
            "result": "success",
        }
        saved_files.append(file_info)

    return response_ok({"files": saved_files})


@system_bp.get("/down-file/<file_id>")
@openapi.definition(
    summary="下载文件",
    description="下载文件",
    parameter=[
        {
            "name": "file_id",
            "in": "path",
            "description": "文件名（上传时返回的 file_id）",
            "required": True,
            "schema": {"type": "string"},
        },
        {
            "name": "download_name",
            "in": "query",
            "description": "下载文件名",
            "required": False,
            "schema": {"type": "string"},
        },
    ],
    response={
        "200": {
            "description": "下载成功",
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def download_file(request, file_id):
    if not file_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
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
        headers = {
            "Content-Disposition": f"attachment; filename=\"{download_name}\";filename*=UTF-8''{quoted_filename}",
            "Content-Type": "application/octet-stream; charset=utf-8",
        }

        try:
            return await response.file(tmp_path, mime_type="application/octet-stream", headers=headers)
        finally:
            await asyncio.to_thread(os.remove, tmp_path)
    else:
        return response_ok("文件不存在")


@system_bp.post("/init-data")
@openapi.definition(
    summary="初始化剧本和应用",
    description="初始化剧本和应用（不受权限控制），支持维护模式",
    body={
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "导入ZIP包",
                    },
                    "maintenance_mode": {
                        "type": "string",
                        "description": "是否上传到维护模式（true/false），默认false",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "导入成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "object"},
                            "message": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
async def init_data(request):
    file_obj = request.files.get("file")
    if not file_obj:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 获取维护模式参数
    maintenance_mode_param = request.form.get("maintenance_mode", "false")
    maintenance_mode = maintenance_mode_param.lower() == "true"

    tenant_id = request.ctx.session.get("tenant_id")
    creator = request.ctx.session["account_info"]
    admin = await Role.get(id="111111")

    imported = {"playbooks": 0, "tools": 0, "resources": 0, "skipped_versions": 0, "skipped_resources": 0}

    buf = io.BytesIO(file_obj.body)
    with zipfile.ZipFile(buf, "r", metadata_encoding="utf-8") as zf:
        names = zf.namelist()

        # 导入资源
        if "resources.json" in names:
            data = orjson.loads(zf.read("resources.json"))
            for res in data:
                name = res.get("name")
                id_ = res.get("id")
                tool_name = res.get("tool_name")
                engine = res.get("engine")
                resources = res.get("resources") or {}
                exist = await ToolResources.get_or_none(
                    name=name,
                    deleted=False,
                    tool_name=tool_name,
                    internal=maintenance_mode,
                    tenant_id=tenant_id,
                )
                if exist:
                    imported["skipped_resources"] += 1
                    continue
                await ToolResources.create(
                    id=id_,
                    tool_name=tool_name,
                    name=name,
                    engine=engine,
                    resources=orjson.dumps(resources).decode(),
                    internal=maintenance_mode,
                    creator=creator.username,
                    updater=creator.username,
                    tenant_id=tenant_id,
                )
                imported["resources"] += 1

        # 导入工具（安装时无需 License 验证，版本号自动生成）
        mock_lic = {"moduleList": [], "grouplist": {}}

        class MockFile:
            def __init__(self, name, body):
                self.name = name
                self.body = body

        # 如果是维护模式，将标识写入 Redis，供 upload_tool 内部的 unzip 函数读取
        session_id = request.ctx.session["id"]
        if maintenance_mode:
            await request.app.ctx.cache_client.set(session_id, True, 300)  # 5分钟过期

        try:
            for fn in names:
                if not fn.endswith(".zip"):
                    continue

                content = zf.read(fn)
                mock_file = MockFile(name=fn, body=content)
                result = await upload_tool(mock_file, mock_lic, creator.username, tenant_id, request)

                if result and result.get("msg") == "success":
                    imported["tools"] += 1
                elif result and result.get("msg") == "工具已存在":
                    imported["skipped_versions"] += 1
        finally:
            if maintenance_mode:
                await request.app.ctx.cache_client.delete(session_id)

        # 导入剧本
        if "workflow.json" in names:
            flows = orjson.loads(zf.read("workflow.json"))
            for item in flows:
                ver = item.get("version")
                if await PlaybookVersion.filter(version=ver, deleted=False, tenant_id=tenant_id).exists():
                    imported["skipped_versions"] += 1
                    continue
                name = item.get("name")
                remark = item.get("remark", "")
                change_desc = item.get("change_desc", "")
                flow_json = item.get("flow_json") or {}
                async with in_transaction():
                    pb = await Playbook.get_or_none(name=name, deleted=False, tenant_id=tenant_id)
                    if not pb:
                        pb = await Playbook.create(
                            id=str(uuid.uuid4()),
                            name=name,
                            remark=remark,
                            creator=creator,
                            internal=maintenance_mode,
                            tenant_id=tenant_id,
                        )
                        await PlaybookPermissions.create(
                            pb=pb,
                            users=creator,
                            roles=None,
                            permissions=["r", "u", "d", "e", "p"],
                            tenant_id=tenant_id,
                        )
                        await PlaybookPermissions.create(
                            pb=pb,
                            users=None,
                            roles=admin,
                            permissions=["r", "u", "d", "e", "p"],
                            tenant_id=tenant_id,
                        )
                    pv = await PlaybookVersion.create(
                        playbook=pb,
                        version=ver,
                        status=0,
                        change_desc=change_desc,
                        flow_json=flow_json,
                        creator=creator,
                        updator=creator,
                        flow_timeout=300,
                        tenant_id=tenant_id,
                    )
                    action_ids = await statistics_data(flow_json, tenant_id)
                    await tool_action_update({pv.id: action_ids}, tenant_id)
                    imported["playbooks"] += 1

    if imported["skipped_resources"] > 0 or imported["skipped_versions"] > 0:
        raise CustomError(ErrorCode.IMPORT_FLOW_ERROR)
    return response_ok(imported)
