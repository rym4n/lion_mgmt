# coding=utf-8
import asyncio
import base64
import datetime
from collections import defaultdict
from functools import reduce
import hashlib
from io import BytesIO
import operator
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import zipfile

from aiocache import caches
import orjson
from sanic import Blueprint
from sanic.log import logger
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.functions import Max
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs import utils
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.nanobot import NanobotClient
from libs.security.authorization import load_license, requires_permission
from libs.utils import gen_run_id
from nanobot.agent import AgentHook, AgentHookContext
from models import (
    ActionPermissions,
    Config,
    InspectionCategory,
    PlaybookVersion,
    Runlog,
    Tag,
    Timed,
    Tool,
    ToolAction,
    ToolResources,
    ToolResourcesModel,
)
from settings import NANOBOT_WORKSPACE_BASE, TASK_TOPIC, TZ

from PIL import Image

# 定义 Blueprint
tools_bp = Blueprint("tools", url_prefix="/tools")
__all__ = ["tools_bp", "tool_action_update", "tool_action_playbook_delete"]
# 预期的文件结构
EXPECTED_STRUCTURE = {"required_files": ["config.json"]}

ACTION_TYPE_MAP = {
    "string": "text",
    "integer": "text",
    "password": "password",
    "long": "text",
    "date": "date",
    "boolean": "boolean",
    "outside_file": "file",
    "json": "text",
    "jsonarray": "text",
}

connected_clients = {}


def check_file_size(file_obj, max_size_kb=5):
    current_pos = file_obj.tell()
    file_obj.seek(0, 2)
    file_size_bytes = file_obj.tell()

    file_obj.seek(current_pos)

    file_size_kb = file_size_bytes / 1024
    return file_size_kb <= max_size_kb


@tools_bp.post("/upload")
@openapi.definition(
    summary="上传工具接口",
    description="上传ZIP格式的工具包（需要工具创建权限）",
    body={
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "ZIP文件",
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
                                    "tools": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "tool": {"type": "string"},
                                                "msg": {"type": "string"},
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
@requires_permission("tool:c")
async def upload_and_extract_zip(request):
    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

    tenant_id = request.ctx.session.get("tenant_id")

    creator = request.ctx.session["account_info"].username
    if "file" not in request.files:
        await resp.send("data: No file provided\n\n")
        await resp.eof()
        return

    results = []
    # 获取 ZIP 文件
    zip_files = request.files.getlist("file")
    total_files = len(zip_files)

    lic = load_license()
    if not lic:
        raise CustomError(ErrorCode.INVALID_LICENSE_ERROR)

    for index, zip_file in enumerate(zip_files):
        result = await upload_tool(zip_file, lic, creator, tenant_id, request)
        results.append(result)

        progress = int(((index + 1) / total_files) * 100)
        await resp.send(f"data: 解压进度：{progress}%\n\n")

    if resp:
        await resp.send(f"data: {results}\n\n")
        await resp.eof()


async def upload_tool(zip_file, lic, creator, tenant_id, request):
    # 按工具名称去重统计应用数量（同一工具的多个版本只算一个应用）
    tool_names = await Tool.filter(deleted=False, maintain=False).values_list("name", flat=True)
    total_count = len(set(tool_names))
    zip_filename = zip_file.name

    total_up_value = 99999
    for item in lic["moduleList"]:
        if item["module"] == "tool_management_bp":
            if int(lic["grouplist"][item.get("group")]["expireTime"]) > int(time.time() * 1000):
                total_up_value = int(lic["grouplist"][item.get("group")]["value"])

    if total_up_value != 99999 and total_count >= total_up_value:
        return {"tool": zip_filename, "msg": "应用数量已达到上限", "tool_name": ""}

    config_data, version, tool_name, tool_description, msg = await unzip(zip_file.body, creator, request.app.ctx.cache_client, request.ctx.session["id"], tenant_id)
    if msg != "success":
        return {"tool": zip_filename, "msg": msg, "tool_name": tool_description or ""}

    if "actions" in config_data.keys():
        action_key = "actions"
    else:
        return {"tool": zip_filename, "msg": "config文件内容错误，缺失actions字段", "tool_name": tool_description or ""}

    health_check = 0
    for action in config_data.get(action_key):
        if "health_check" == action.get("func"):
            health_check = 1
        await ToolAction.create(tool_name=config_data.get("name"), version_number=version, action=action, tenant_id=tenant_id)
        actions = await ActionPermissions.filter(tool_name=tool_name, deleted=False, tenant_id=tenant_id).values("action_name")
        action_names = {action["action_name"] for action in actions}

        if action["func"] not in action_names:
            await ActionPermissions.create(
                tool_name=tool_name,
                action_name=action["func"],
                action_description=action.get("name") or "",
                roles=[],
                users=[],
                tenant_id=tenant_id,
            )
    await ActionPermissions.create(
        tool_name=tool_name,
        action_name="all",
        action_description="动作全部动作权限",
        roles=[],
        users=[],
        tenant_id=tenant_id,
    )
    configuration = await ToolResourcesModel.get_or_none(tool_name=tool_name, deleted=False, tenant_id=tenant_id)
    if not configuration:
        trm = await ToolResourcesModel.create(
            tool_name=tool_name,
            resources=config_data.get("resource") or {},
            status=(1 if (config_data.get("resource") and any(field.get("required") is True for field in config_data.get("resource").values())) else 0),
            health_check=health_check,
            tenant_id=tenant_id,
        )
        if health_check == 1:
            trm.version = version
            await trm.save()
    else:
        if configuration.health_check != 1 and health_check == 1:
            configuration.health_check = health_check
            configuration.version = version
        configuration.resources = config_data.get("resource")
        await configuration.save()

    return {"tool": zip_filename, "msg": msg, "tool_name": tool_description}


def open_zip(zip_buffer):
    try:
        return zipfile.ZipFile(zip_buffer, "r", metadata_encoding="utf-8")
    except UnicodeDecodeError:
        zip_buffer.seek(0)
        return zipfile.ZipFile(zip_buffer, "r", metadata_encoding="gbk")


def convert_wz_config(data):
    """转换WZ格式的config.json到AIOE格式"""
    new_data = {
        "name": data.get("name"),
        "main": data.get("jar"),
        "logo": data.get("logo"),
        "description": data.get("description"),
        "internal": data.get("internal", False),
        "resource": data.get("configuration", {}),
        "actions": [],
    }
    for a in data.get("actions", []):
        tmp = {
            "name": a.get("description") or a.get("name"),
            "func": a.get("class_name") or a.get("func"),
            "classify": a.get("classify", "query"),
            "input": [],
        }
        for k, v in (a.get("parameters") or {}).items():
            tmp["input"].append(
                {
                    "key": k,
                    "type": ACTION_TYPE_MAP.get(v.get("data_type"), "text"),
                    "required": v.get("required", False),
                    "description": v.get("description") or k,
                    "order": v.get("order", 0),
                }
            )
        new_data["actions"].append(tmp)
    return new_data


def convert_logo_to_ico(logo_data):
    """将logo转换为ICO格式 (32x32)"""
    try:
        with Image.open(BytesIO(logo_data)) as img:
            if img.format == "JPEG":
                rgba_img = Image.new("RGBA", img.size)
                rgba_img.paste(img, (0, 0))
                img = rgba_img
            else:
                img = img.convert("RGBA")

            width, height = img.size
            max_size = 32

            if width != height:
                canvas_size = max_size
                position = ((canvas_size - min(width, max_size)) // 2, (canvas_size - min(height, max_size)) // 2)
                new_img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
                resized_img = img.copy()
                resized_img.thumbnail((max_size, max_size), Image.LANCZOS)
                new_img.paste(resized_img, position, resized_img)
                img = new_img
            else:
                img.thumbnail((max_size, max_size), Image.LANCZOS)

            out_buffer = BytesIO()
            img.save(out_buffer, format="ICO", optimize=True, compress_level=9, sizes=[(max_size, max_size)])
            return out_buffer.getvalue()
    except Exception:
        return logo_data


async def unzip(zip_data, creator, redis_client, token_id, tenant_id):
    config_data = None
    tool_name = None
    tool_description = None
    try:
        zip_buffer = BytesIO(zip_data)
        logo_data = None

        with open_zip(zip_buffer) as zip_ref:
            version = None

            files_info = zip_ref.infolist()

            actual_files = {os.path.basename(f.filename) for f in files_info if not f.is_dir()}
            py_files_dict = {os.path.basename(f.filename): f for f in files_info if f.filename.endswith(".py")}
            config_path = next(
                (f for f in files_info if os.path.basename(f.filename) == "config.json"),
                None,
            )

            if config_path is None:
                return None, None, None, None, "config文件不存在"

            if config_path.filename.replace("\\", "/") != "config.json":
                return None, None, None, None, "config文件需要在最外层目录下"

            with zip_ref.open(config_path) as config_file:
                config_data = orjson.loads(config_file.read().decode())

            # 转换WZ格式到AIOE格式
            is_wz_format = "jar" in config_data and "main" not in config_data
            if is_wz_format:
                original_config = config_data.copy()
                config_data = convert_wz_config(config_data)
                # 重新构建ZIP以符合新格式 (将config.json放在根目录，转换logo为ICO)
                new_zip_buffer = BytesIO()
                with zipfile.ZipFile(new_zip_buffer, "w", zipfile.ZIP_DEFLATED) as new_zip:
                    # 获取新logo名称
                    new_logo_name = config_data.get("logo")
                    if "/" in new_logo_name:
                        new_logo_name = new_logo_name.split("/")[-1]
                    if not new_logo_name.lower().endswith(".ico"):
                        new_logo_name = os.path.splitext(new_logo_name)[0] + ".ico"
                    config_data["logo"] = new_logo_name

                    # 写入新config.json
                    new_zip.writestr("config.json", orjson.dumps(config_data, option=orjson.OPT_INDENT_2))

                    # 遍历原ZIP文件并写入新ZIP
                    for info in files_info:
                        if os.path.basename(info.filename) == "config.json":
                            continue

                        with zip_ref.open(info) as f:
                            file_data = f.read()
                            # 处理logo转换
                            if info.filename == original_config.get("logo") or os.path.basename(info.filename) == os.path.basename(original_config.get("logo")):
                                logo_data = convert_logo_to_ico(file_data)
                                new_zip.writestr(new_logo_name, logo_data)
                                continue

                            # 将其他文件放在根目录或保持原样
                            target_filename = os.path.basename(info.filename)
                            if target_filename and not info.is_dir():
                                if target_filename not in new_zip.namelist():
                                    new_zip.writestr(target_filename, file_data)

                zip_data = new_zip_buffer.getvalue()
                zip_buffer = BytesIO(zip_data)
                # 重新打开新ZIP以更新files_info等
                zip_ref.close()
                zip_ref = open_zip(zip_buffer)
                files_info = zip_ref.infolist()
                actual_files = {os.path.basename(f.filename) for f in files_info if not f.is_dir()}
                py_files_dict = {os.path.basename(f.filename): f for f in files_info if f.filename.endswith(".py")}

            tool_name = config_data.get("name")
            tool_description = config_data.get("description")
            pattern = r"^[A-Za-z][A-Za-z0-9_]*$"
            if not bool(re.match(pattern, tool_name)):
                return config_data, version, tool_name, tool_description, "工具名称不合法"

            for info in files_info:
                if ".." in info.filename or info.filename.startswith("/"):
                    return config_data, version, tool_name, tool_description, "文件名包含非法字符"

            if "description" not in config_data.keys():
                return (
                    config_data,
                    version,
                    tool_name,
                    tool_description,
                    "config文件内容错误，缺失description字段",
                )

            hash_func = getattr(hashlib, "sha1", None)
            if hash_func is None:
                return (
                    config_data,
                    version,
                    tool_name,
                    tool_description,
                    "Unsupported hash type. Use 'md5', 'sha1', 'sha256', or 'sha512'.",
                )

            hasher = hash_func()
            zip_buffer.seek(0)  # 确保从头开始读取数据
            for chunk in iter(lambda: zip_buffer.read(4096), b""):
                hasher.update(chunk)

            if not config_path:
                return config_data, version, tool_name, tool_description, "缺失config文件"

            missing_files = set(EXPECTED_STRUCTURE["required_files"]) - actual_files
            if missing_files:
                return config_data, version, tool_name, tool_description, "缺失config文件"

            name = config_data.get("name")
            py_file_name = config_data.get("main")
            logo_name = config_data.get("logo")
            if "/" in logo_name:
                logo_name = logo_name.split("/")[-1]

            if "actions" in config_data.keys():
                action_key = "actions"
            else:
                return (
                    config_data,
                    version,
                    tool_name,
                    tool_description,
                    "config文件内容错误，缺失actions字段",
                )
            class_name_list = [action.get("func") for action in config_data.get(action_key, [])]

            for action in config_data.get(action_key, []):
                if not all(k in action for k in ["func", "name", "input"]):
                    return config_data, version, tool_name, tool_description, "actions字段内容错误"

                inputs = action.get("input")
                if not isinstance(inputs, list):
                    return config_data, version, tool_name, tool_description, "input字段内容错误"

                for input_item in inputs:
                    if not all(k in input_item for k in ["key", "type", "required", "description"]):
                        return config_data, version, tool_name, tool_description, "input字段内容错误"

                    if any(input_item[k] in (None, "") for k in ["key", "type", "required", "description"]):
                        return config_data, version, tool_name, tool_description, "input字段内容错误"

            if not py_file_name:
                return config_data, version, tool_name, tool_description, "config文件内容错误"

            if py_file_name not in py_files_dict:
                return config_data, version, tool_name, tool_description, "缺失py文件"

            if logo_name not in actual_files:
                return config_data, version, tool_name, tool_description, "缺少logo文件"

            logo_found = False
            for file_info in files_info:
                if file_info.filename.lower().endswith((".png", ".jpg", ".jpeg", ".ico")):
                    logo_found = True
                    with zip_ref.open(file_info) as logo_file:
                        if not check_file_size(logo_file):
                            return (
                                config_data,
                                version,
                                tool_name,
                                tool_description,
                                "logo文件大小超过5KB限制，请压缩后重新上传",
                            )
                        logo_data = logo_file.read()

            if not logo_found:
                return config_data, version, tool_name, tool_description, "缺少logo文件"

            with zip_ref.open(py_files_dict[py_file_name]) as py_file:
                py_data = py_file.read().decode()

            if any(f"def {class_name}(" not in py_data for class_name in class_name_list):
                return config_data, version, tool_name, tool_description, "Python文件内容错误"

            configuration = await ToolResourcesModel.filter(tool_name=name, deleted=False, tenant_id=tenant_id).first().values("resources")
            if configuration and configuration["resources"] != config_data.get("resource", {}):
                for key in config_data.get("resource", {}).keys():
                    if key not in configuration["resources"].keys() and config_data.get("resource", {})[key].get("required") is True:
                        return (
                            config_data,
                            version,
                            tool_name,
                            tool_description,
                            "工具资源不一致，请新建工具或删除原工具后再次上传",
                        )
                    if key in configuration["resources"].keys() and config_data.get("resource", {})[key].get("data_type") != configuration["resources"][key].get("data_type"):
                        return (
                            config_data,
                            version,
                            tool_name,
                            tool_description,
                            "工具资源不一致，请新建工具或删除原工具后再次上传",
                        )

            if await Tool.filter(name=name, deleted=False, tenant_id=tenant_id, version=hasher.hexdigest()).exists():
                return config_data, version, tool_name, tool_description, "工具已存在"

            version = hasher.hexdigest()
            suffix = logo_name.split(".")[-1]
            save_path = await utils.upload_to_s3(zip_data, "system", f"{tool_name}/{version}.zip", "tools")
            logo_path = await utils.upload_to_s3(logo_data, "user", f"{tool_name}.{suffix}", "tools")

            maintenance_mode = await redis_client.get(token_id)
            tool = await Tool.create(
                name=name,
                version=version,
                path=save_path,
                logo_path=logo_path,
                description=config_data.get("description"),
                creator=creator,
                updater=creator,
                hash_value=hasher.hexdigest(),
                internal=config_data.get("internal", False),
                main=py_file_name,
                maintain=maintenance_mode if maintenance_mode else False,
                tenant_id=tenant_id,
            )

            # 将config数据存入redis
            await redis_client.set(name + "_" + version, config_data, 2592000)

            return config_data, version, tool.name, tool_description, "success"

    except zipfile.BadZipFile as e:
        return config_data, version, tool_name, tool_description, str(e)


@tools_bp.post("/all")
@openapi.definition(
    summary="获取所有工具列表",
    description="获取所有工具列表（仅显示每个工具的最新版本，并对工具名称进行模糊搜索），需要工具查看权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码（默认1）"},
                    "size": {"type": "integer", "description": "每页数量（默认10）"},
                    "key": {
                        "type": "string",
                        "description": "名称和描述的搜索（可选，模糊搜索）",
                    },
                    "tag": {
                        "type": "string",
                        "description": "标签名称（可选，精确搜索）",
                    },
                    "logo": {
                        "type": "integer",
                        "description": "是否需要logo数据 0为不需要，1为需要",
                    },
                    "available_only": {
                        "type": "boolean",
                        "description": "是否只返回可用的工具（可用定义为：存在资源或无需资源）",
                    },
                },
            }
        }
    },
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
                                    "tools": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "name": {"type": "string"},
                                                "version": {"type": "string"},
                                                "description": {"type": "string"},
                                                "logo": {"type": "string"},
                                                "required": {"type": "integer"},
                                                "health_check": {"type": "integer"},
                                                "health_check_version": {"type": "string"},
                                            },
                                        },
                                    },
                                    "total": {"type": "integer"},
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total_pages": {"type": "integer"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:r")
async def all_tools(request):
    req_json = request.json or {}
    page = int(req_json.get("page", 1))
    size = int(req_json.get("size", 15))
    key = req_json.get("key", "").strip()
    available_only = req_json.get("available_only", False)

    # if size > 50:
    #     raise CustomError(ErrorCode.REQUEST_TOO_MANY_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    subquery = Tool.filter(deleted=False, tenant_id=tenant_id).annotate(latest_ctime=Max("ctime")).group_by("name")
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    subquery = subquery.filter(maintain=maintenance_mode if maintenance_mode else False)
    if key:
        actions = await ToolAction.filter(deleted=False, tenant_id=tenant_id).values("tool_name", "version_number", "action")
        tool_names_from_actions = set()

        for act in actions:
            act_data = act["action"]
            if isinstance(act_data, dict) and (key.lower() in act_data.get("func", "").lower() or key.lower() in act_data.get("name", "").lower()):
                tool_names_from_actions.add(act["tool_name"])

        cond = Q(name__icontains=key) | Q(description__icontains=key)
        # 由于后面使用了 group_by("name")，只需要匹配工具名称即可，不需要精确匹配版本
        # 这样可以大大简化查询条件，避免递归深度超限，同时保证查询结果完整
        if tool_names_from_actions:
            cond |= Q(name__in=list(tool_names_from_actions))
        subquery = subquery.filter(cond)
    if req_json.get("tag"):
        subquery = subquery.filter(tag__contains=[req_json.get("tag")])
    subquery_results = await subquery.values("name", "latest_ctime")
    if not subquery_results:
        return response_ok({"tools": [], "total": 0, "page": page, "size": size, "total_pages": 0})

    # 如果需要筛选可用工具
    if available_only:
        # 获取所有工具的资源模型
        tool_names = [pair["name"] for pair in subquery_results]
        tool_resources_model = await ToolResourcesModel.filter(
            tool_name__in=tool_names,
            deleted=False,
            tenant_id=tenant_id
        ).values("tool_name", "status")

        # 构建工具资源状态映射
        tool_resource_map = {tr["tool_name"]: tr["status"] for tr in tool_resources_model}

        # 获取所有实际存在的资源（用于判断status=1的工具是否有资源）
        actual_resources = await ToolResources.filter(
            tool_name__in=tool_names,
            deleted=False,
            tenant_id=tenant_id
        ).values_list("tool_name", flat=True)
        tools_with_resources = set(actual_resources)

        # 过滤出可用的工具
        # 可用定义：
        # 1. 无需资源（status=0 或不存在资源模型）
        # 2. 存在资源（status=1 且 ToolResources 中存在该工具的资源）
        available_tools = []
        for pair in subquery_results:
            tool_name = pair["name"]
            tool_status = tool_resource_map.get(tool_name)

            if tool_status is None or tool_status == 0:
                # 没有资源模型或status=0，认为可用
                available_tools.append(pair)
            elif tool_status == 1 and tool_name in tools_with_resources:
                # status=1 且存在实际资源，认为可用
                available_tools.append(pair)

        subquery_results = available_tools

        if not subquery_results:
            return response_ok({"tools": [], "total": 0, "page": page, "size": size, "total_pages": 0})

    # 使用 reduce 避免循环中的深度嵌套
    if subquery_results:
        conditions = [Q(name=pair["name"], ctime=pair["latest_ctime"]) for pair in subquery_results]
        condition = reduce(operator.or_, conditions)
    else:
        condition = Q()

    latest_tools = await Tool.filter(condition, deleted=False, tenant_id=tenant_id).offset((page - 1) * size).limit(size).values()
    for tool in latest_tools:
        if int(req_json.get("logo", 1)) == 1 and tool["logo_path"]:
            tool["logo"] = tool["logo_path"]
        tool.pop("path", None)
        trm = await ToolResourcesModel.get_or_none(deleted=False, tool_name=tool["name"], tenant_id=tenant_id)
        if trm:
            tool["required"] = trm.status
            tool["health_check"] = trm.health_check
            tool["health_check_version"] = trm.version

    total_count = await Tool.filter(condition, deleted=False, tenant_id=tenant_id).count()

    return response_ok(
        {
            "tools": latest_tools,
            "total": total_count,
            "page": page,
            "size": size,
            "total_pages": (total_count + size - 1) // size,
        }
    )


@tools_bp.post("/versions")
@openapi.definition(
    summary="获取具体工具的所有版本列表",
    description="获取具体工具的所有版本列表（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "description": "工具名称"}},
            }
        }
    },
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
                                        "id": {"type": "string"},
                                        "name": {"type": "string"},
                                        "version": {"type": "string"},
                                        "description": {"type": "string"},
                                        "creator": {"type": "string"},
                                        "updater": {"type": "string"},
                                        "ctime": {"type": "string"},
                                        "utime": {"type": "string"},
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
@requires_permission("tool:r")
async def all_versions(request):
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    tools = (
        await Tool.filter(
            name=name,
            deleted=False,
            maintain=maintenance_mode if maintenance_mode else False,
            tenant_id=tenant_id,
        )
        .order_by("-ctime")
        .values()
    )

    for tool in tools:
        tool.pop("path")

    return response_ok(tools)


@tools_bp.post("/actions")
@openapi.definition(
    summary="获取工具当前版本的所有动作列表",
    description="获取工具当前版本的所有动作列表（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "version"],
                "properties": {
                    "name": {"type": "string", "description": "工具名称"},
                    "version": {"type": "string", "description": "工具版本号"},
                },
            }
        }
    },
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
                                    "actions": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "func": {"type": "string"},
                                                "name": {"type": "string"},
                                                "input": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "key": {"type": "string"},
                                                            "type": {"type": "string"},
                                                            "required": {"type": "boolean"},
                                                            "description": {"type": "string"},
                                                        },
                                                    },
                                                },
                                                "id": {"type": "string"},
                                                "playbook": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "pv_id": {"type": "string"},
                                                            "name": {"type": "string"},
                                                            "version": {"type": "string"},
                                                            "status": {"type": "integer"},
                                                        },
                                                    },
                                                },
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
@requires_permission("tool:r")
async def tool_actions(request):
    name = request.json.get("name")
    version = request.json.get("version")
    if not name or not version:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    tool = await Tool.get_or_none(name=name, version=version, deleted=False, tenant_id=tenant_id)
    if not tool:
        raise CustomError(ErrorCode.TOOL_NOT_FOUND_ERROR)

    actions = await ToolAction.filter(tool_name=name, version_number=version, tenant_id=tenant_id).values("action", "id", "playbook")
    action_list = []
    for action in actions:
        action.get("action")["id"] = action.get("id")
        pv_list = []
        if action.get("playbook"):
            for pv_id in action.get("playbook"):
                pv = await PlaybookVersion.get_or_none(id=pv_id, deleted=False, tenant_id=tenant_id).prefetch_related("playbook")
                pv_list.append(
                    {
                        "id": pv.playbook.id,
                        "pv_id": pv_id,
                        "name": pv.playbook.name,
                        "version": pv.version,
                        "status": pv.status,
                    }
                )
        action.get("action")["playbook"] = pv_list
        action_list.append(action.get("action"))

    return response_ok({"actions": action_list})


@tools_bp.post("/actions/filter")
@openapi.definition(
    summary="获取工具当前版本动作列表（支持筛选）",
    description="获取工具当前版本动作列表，支持按动作类型筛选（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["classify"],
                "properties": {
                    "classify": {"type": "string", "description": "动作类型"},
                },
            }
        }
    },
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
                                    "actions": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "func": {"type": "string"},
                                                "name": {"type": "string"},
                                                "classify": {"type": "string"},
                                                "input": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "key": {"type": "string"},
                                                            "type": {"type": "string"},
                                                            "required": {"type": "boolean"},
                                                            "description": {"type": "string"},
                                                        },
                                                    },
                                                },
                                                "id": {"type": "string"},
                                                "playbook": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "pv_id": {"type": "string"},
                                                            "name": {"type": "string"},
                                                            "version": {"type": "string"},
                                                            "status": {"type": "integer"},
                                                        },
                                                    },
                                                },
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
@requires_permission("tool:r")
async def tool_actions_filter(request):
    classify = request.json.get("classify")

    tenant_id = request.ctx.session.get("tenant_id")

    actions = await ToolAction.filter(tenant_id=tenant_id).values("action", "id", "tool_name", "version_number")
    action_list = []
    for action in actions:
        act_data = action.get("action")
        if classify and act_data.get("classify") != classify:
            continue

        act_data["id"] = action.get("id")
        act_data["tool_name"] = action.get("tool_name")
        act_data["version"] = action.get("version_number")
        tool = await Tool.get_or_none(name=action.get("tool_name"), version=action.get("version_number"), deleted=False, tenant_id=tenant_id)
        act_data["description"] = tool.description
        act_data["tool_main"] = tool.main
        action_list.append(act_data)

    return response_ok({"actions": action_list})


@tools_bp.post("/upstatus")
@openapi.definition(
    summary="更新工具状态",
    description="更新工具状态（需要工具更新权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "version", "status"],
                "properties": {
                    "name": {"type": "string", "description": "工具名称"},
                    "version": {"type": "string", "description": "工具版本号"},
                    "status": {
                        "type": "integer",
                        "description": "工具状态（1:有 0:无）",
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
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:u")
async def update_tool_status(request):
    name = request.json.get("name")
    version = request.json.get("version")
    status = int(request.json.get("status"))
    if not name or not version or status not in [0, 1]:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    tool = await Tool.get_or_none(name=name, version=version, deleted=False, tenant_id=tenant_id)
    if not tool:
        raise CustomError(ErrorCode.TOOL_NOT_FOUND_ERROR)

    tool.status = status
    await tool.save()

    return response_ok({"message": "工具状态更新成功"})


@tools_bp.post("/delete/version")
@openapi.definition(
    summary="删除指定版本工具",
    description="删除指定版本工具（需要工具删除权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "version"],
                "properties": {
                    "name": {"type": "string", "description": "工具名称"},
                    "version": {"type": "string", "description": "工具版本号"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:d")
async def delete_tool(request):
    name = request.json.get("name")
    version = request.json.get("version")
    if not name or not version:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    async with in_transaction():
        tool = await Tool.get(name=name, version=version, deleted=False, tenant_id=tenant_id)
        if not tool:
            return response_ok({"message": "工具删除成功"})

        if tool.status == 1:
            raise CustomError(ErrorCode.TOOL_HAS_SCRIPT_ERROR)

        if tool.maintain:
            raise CustomError(ErrorCode.INTERNAL_DELETE_ERROR)

        if await Tool.filter(name=name, deleted=False, tenant_id=tenant_id).count() == 1:
            await ToolResourcesModel.filter(tool_name=name, deleted=False, tenant_id=tenant_id).update(deleted=True)
            await ToolResources.filter(tool_name=name, deleted=False, tenant_id=tenant_id).update(deleted=True)
            await ActionPermissions.filter(tool_name=name, deleted=False, tenant_id=tenant_id).update(deleted=True)

        await utils.delete_from_s3("system", f"{name}/{version}.zip", "tools")

        await ToolAction.filter(tool_name=name, version_number=version, deleted=False, tenant_id=tenant_id).update(deleted=True)
        tool.deleted = True
        tool.updater = request.ctx.session["account_info"].username
        await ToolAction.filter(tool_name=name, version_number=version, deleted=False, tenant_id=tenant_id).update(deleted=True)
        await tool.save()

    return response_ok({"message": "工具删除成功"})


@tools_bp.post("/delete/all")
@openapi.definition(
    summary="删除工具全部版本",
    description="删除工具全部版本（需要工具删除权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "description": "工具名称"}},
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:d")
async def delete_tool_all(request):
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    if await Tool.filter(name=name, deleted=False, maintain=True, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.INTERNAL_DELETE_ERROR)

    if await Tool.filter(name=name, deleted=False, status=1, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.TOOL_HAS_SCRIPT_ERROR)

    async with in_transaction():
        for tool in await Tool.filter(name=name, deleted=False, tenant_id=tenant_id).all():
            await utils.delete_from_s3("system", f"{name}/{tool.version}.zip", "tools")

        await ToolAction.filter(tool_name=name, deleted=False, tenant_id=tenant_id).update(deleted=True)
        await ToolResourcesModel.filter(tool_name=name, deleted=False, tenant_id=tenant_id).update(deleted=True)
        await ToolResources.filter(tool_name=name, deleted=False, tenant_id=tenant_id).update(deleted=True)
        await ActionPermissions.filter(tool_name=name, deleted=False, tenant_id=tenant_id).update(deleted=True)
        await Tool.filter(name=name, deleted=False, tenant_id=tenant_id).update(deleted=True)

    return response_ok({"message": "全部工具删除成功"})


@tools_bp.post("/resource/model")
@openapi.definition(
    summary="获取工具的资源模型",
    description="获取工具的资源模型（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "description": "工具name"}},
            }
        }
    },
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
                                    "id": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "tool_name": {"type": "string"},
                                    "resources": {"type": "object"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:r")
async def tool_resource_model(request):
    tool_name = request.json.get("name")
    if not tool_name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    resources = await ToolResourcesModel.filter(tool_name=tool_name, deleted=False, tenant_id=tenant_id).first().values()
    if not resources:
        raise CustomError(ErrorCode.RESOURCES_MODEL_NOT_FOUND_ERROR)

    return response_ok(resources)


@tools_bp.post("/resource/add")
@openapi.definition(
    summary="新增工具资源",
    description="新增工具资源（需要工具创建权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["tool_name", "name", "resources"],
                "properties": {
                    "tool_name": {"type": "string", "description": "工具名称"},
                    "name": {"type": "string", "description": "资源名称"},
                    "engine": {"type": "string", "description": "执行引擎"},
                    "resources": {"type": "object", "description": "资源模板"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "新增成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                    "id": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:c")
async def add_tool_resource(request):
    tool_name = request.json.get("tool_name")
    name = request.json.get("name")
    engine = request.json.get("engine")
    resources = request.json.get("resources")
    if not tool_name or resources is None or not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    existing_resource = await ToolResources.get_or_none(
        name=name,
        deleted=False,
        tool_name=tool_name,
        internal=maintenance_mode if maintenance_mode else False,
        tenant_id=tenant_id,
    )
    if existing_resource:
        raise CustomError(ErrorCode.RESOURCES_NAME_EXISTS_ERROR)

    tr = await ToolResources.create(
        tool_name=tool_name,
        name=name,
        engine=engine,
        resources=orjson.dumps(resources).decode(),
        internal=maintenance_mode if maintenance_mode else False,
        creator=request.ctx.session["account_info"].username,
        updater=request.ctx.session["account_info"].username,
        tenant_id=tenant_id,
    )

    r = {k: resources[k].get("value") or resources[k].get("default_value", "") for k in resources}
    r[tr.id + "_name"] = name
    await caches.get("redis_chat").set("resource_" + tr.id, r)

    return response_ok({"message": "工具资源新增成功", "id": tr.id})


@tools_bp.post("/resource/detailed")
@openapi.definition(
    summary="获取工具的资源详情",
    description="获取工具的资源详情（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "description": "工具名称"}},
            }
        }
    },
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
                                        "id": {"type": "string"},
                                        "ctime": {"type": "string"},
                                        "utime": {"type": "string"},
                                        "tool_name": {"type": "string"},
                                        "name": {"type": "string"},
                                        "engine": {"type": "string"},
                                        "resources": {"type": "object"},
                                        "creator": {"type": "string"},
                                        "updater": {"type": "string"},
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
@requires_permission("tool:r")
async def tool_resources_detailed(request):
    tool_name = request.json.get("name")
    if not tool_name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    resources = await ToolResources.filter(
        tool_name=tool_name,
        deleted=False,
        internal=maintenance_mode if maintenance_mode else False,
        tenant_id=tenant_id,
    ).all()
    return response_ok([r.json for r in resources])


@tools_bp.post("/resource/update")
@openapi.definition(
    summary="更新工具资源",
    description="更新工具资源（需要工具更新权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["tool_name", "name"],
                "properties": {
                    "tool_name": {"type": "string", "description": "工具名称"},
                    "name": {"type": "string", "description": "资源名称"},
                    "engine": {"type": "string", "description": "资源执行引擎"},
                    "resources": {"type": "object", "description": "资源模板"},
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
                            "data": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:u")
async def update_tool_resource(request):
    tool_name = request.json.get("tool_name")
    name = request.json.get("name")
    engine = request.json.get("engine")
    resources = request.json.get("resources") or {}
    if not tool_name or not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    tool_resource = await ToolResources.get_or_none(
        tool_name=tool_name,
        name=name,
        deleted=False,
        internal=maintenance_mode if maintenance_mode else False,
        tenant_id=tenant_id,
    )

    if not tool_resource:
        raise CustomError(ErrorCode.RESOURCES_NOT_FOUND_ERROR)

    if engine:
        tool_resource.engine = engine

    if resources:
        resources_old = tool_resource.resources_json
        for key, resource in resources.items():
            if resource.get("data_type") == "password" and resource.get("value") == "******":
                resource["value"] = resources_old[key]["value"]
        tool_resource.resources = orjson.dumps(resources).decode()
        r = {k: resources[k].get("value") or resources[k].get("default_value", "") for k in resources}
        r[tool_resource.id + "_name"] = name
        await caches.get("redis_chat").set("resource_" + tool_resource.id, r)

    tool_resource.updater = request.ctx.session["account_info"].username
    update_fields = ["updater"]
    if engine:
        update_fields.append("engine")
    if resources:
        update_fields.append("resources")
    await tool_resource.save(update_fields=update_fields)
    return response_ok({"message": "工具资源更新成功"})


def _contains_resource_id(value, resource_id):
    if isinstance(value, dict):
        return any(_contains_resource_id(item, resource_id) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_resource_id(item, resource_id) for item in value)
    if isinstance(value, str):
        return any(item.strip() == resource_id for item in value.split(","))
    return False


@tools_bp.post("/resource/delete")
@openapi.definition(
    summary="删除工具资源",
    description="删除工具资源（需要工具删除权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["tool_name", "name"],
                "properties": {
                    "tool_name": {"type": "string", "description": "工具名称"},
                    "name": {"type": "string", "description": "资源名称"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:d")
async def delete_tool_resource(request):
    tool_name = request.json.get("tool_name")
    name = request.json.get("name")
    if not tool_name or not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    tool_resource = await ToolResources.get_or_none(
        tool_name=tool_name,
        name=name,
        deleted=False,
        internal=maintenance_mode if maintenance_mode else False,
        tenant_id=tenant_id,
    )

    if not tool_resource:
        raise CustomError(ErrorCode.RESOURCES_NOT_FOUND_ERROR)

    playbooks = await ToolAction.filter(tool_name=tool_name, deleted=False, tenant_id=tenant_id).all().values("playbook")

    playbook_ids = []
    for playbook in playbooks:
        playbook_ids += playbook.get("playbook") if playbook.get("playbook") else []

    playbooks = await PlaybookVersion.filter(id__in=playbook_ids, deleted=False, tenant_id=tenant_id).all().prefetch_related("playbook")

    for playbook in playbooks:
        if playbook.flow_json:
            if tool_resource.id in str(playbook.flow_json):
                return response_ok({"message": f"当前资源已被剧本{playbook.playbook.name}使用，请仔细检查后再删除！"})

    timeds = await Timed.filter(deleted=False, tenant_id=tenant_id).all().prefetch_related("playbook")

    for timed in timeds:
        if timed.type == 0:
            playbook_version = await PlaybookVersion.get_or_none(
                playbook=timed.playbook,
                version=timed.playbook_version,
                deleted=False,
                tenant_id=tenant_id,
            )
            if playbook_version and _contains_resource_id(playbook_version.flow_json, tool_resource.id):
                return response_ok({"message": f"当前资源已被定时任务{timed.name}使用，请仔细检查后再删除！"})
        elif timed.action_input and _contains_resource_id(timed.action_input.get("resource"), tool_resource.id):
            return response_ok({"message": f"当前资源已被定时任务{timed.name}使用，请仔细检查后再删除！"})

    # 检查巡检类别是否使用该资源
    inspection_categories = await InspectionCategory.filter(deleted=False, tenant_id=tenant_id).all()

    for category in inspection_categories:
        if category.resource_ids and tool_resource.id in category.resource_ids:
            return response_ok({"message": f"当前资源已被巡检类别{category.name}使用，请仔细检查后再删除！"})

    tool_resource.deleted = True
    await tool_resource.save(update_fields=["deleted"])

    await caches.get("redis_chat").delete("resource_" + tool_resource.id)

    return response_ok({"message": "工具资源删除成功"})


@tools_bp.post("/download")
@openapi.definition(
    summary="下载工具 ZIP 文件",
    description="下载工具 ZIP 文件接口（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["tool_id"],
                "properties": {"tool_id": {"type": "string", "description": "工具ID"}},
            }
        }
    },
    response={"200": {"description": "下载成功"}},
)
@requires_permission("tool:r")
async def download_zip(request):
    tool_id = request.json.get("tool_id")
    if not tool_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    tool = await Tool.get_or_none(id=tool_id, deleted=False, tenant_id=tenant_id)
    if not tool:
        raise CustomError(ErrorCode.TOOL_NOT_FOUND_ERROR)

    if tool.maintain:
        raise CustomError(ErrorCode.INTERNAL_TOOL_ERROR)

    if tool.internal:
        raise CustomError(ErrorCode.NOT_DOWNLOAD_ERROR)

    file_url = await utils.get_url_from_s3("system", f"{tool.name}/{tool.version}.zip", request.host, "tools")

    file_url = file_url.replace("https://" + request.host, "")

    return response_ok({"url": file_url})


@tools_bp.post("/action/permissions/add")
@openapi.definition(
    summary="新增工具动作权限",
    description="新增工具动作权限（需要工具更新权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "resource_id", "users_id", "roles_id"],
                "properties": {
                    "name": {"type": "string", "description": "工具名称"},
                    "resource_id": {"type": "string", "description": "资源id"},
                    "actions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "动作名称，默认all",
                    },
                    "users_id": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "用户ID列表",
                    },
                    "roles_id": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "角色ID列表",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "新增成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:u")
async def add_permission(request):
    name = request.json.get("name")
    resource_id = request.json.get("resource_id")
    actions = request.json.get("actions", ["all"])
    users_id = list(set(request.json.get("users_id", [])))
    roles_id = list(set(request.json.get("roles_id", [])))
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if not isinstance(users_id, list) or not isinstance(roles_id, list) or not isinstance(actions, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    if "all" in actions:
        if not await ActionPermissions.filter(tool_name=name, action_name="all", deleted=False, tenant_id=tenant_id).exists():
            await ActionPermissions.create(
                tool_name=name,
                action_name="all",
                action_description="动作全部动作权限",
                roles=[],
                users=[],
                tenant_id=tenant_id,
            )
        permissions = await ActionPermissions.filter(tool_name=name, action_name="all", deleted=False, tenant_id=tenant_id).all()
    else:
        permissions = await ActionPermissions.filter(tool_name=name, action_name__in=actions, deleted=False, tenant_id=tenant_id).all()

    if not permissions:
        raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)

    for permission in permissions:
        user_dict_list = []
        if isinstance(permission.users, list):
            user_dict_list = permission.users.copy()
        elif isinstance(permission.users, str):
            try:
                user_dict_list = orjson.loads(permission.users)
            except orjson.JSONDecodeError:
                user_dict_list = []

        if not user_dict_list or not isinstance(user_dict_list[0], dict):
            user_dict_list = [{}]

        user_dict_list[0][resource_id] = users_id

        role_dict_list = []
        if isinstance(permission.roles, list):
            role_dict_list = permission.roles.copy()
        elif isinstance(permission.roles, str):
            try:
                role_dict_list = orjson.loads(permission.roles)
            except orjson.JSONDecodeError:
                role_dict_list = []

        if not role_dict_list or not isinstance(role_dict_list[0], dict):
            role_dict_list = [{}]

        role_dict_list[0][resource_id] = roles_id

        permission.users = user_dict_list
        permission.roles = role_dict_list

    await ActionPermissions.bulk_update(permissions, fields=["users", "roles"])

    return response_ok({"message": "工具动作权限新增成功"})


@tools_bp.post("/action/permissions/select")
@openapi.definition(
    summary="获取工具全部动作权限",
    description="获取工具全部动作权限（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "resource_id"],
                "properties": {
                    "name": {"type": "string", "description": "工具名称"},
                    "resource_id": {"type": "string", "description": "资源id"},
                    "actions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "动作名称，默认all",
                    },
                },
            }
        }
    },
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
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "users_id": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "roles_id": {
                                            "type": "array",
                                            "items": {"type": "string"},
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
@requires_permission("tool:r")
async def select_permission_all(request):
    name = request.json.get("name")
    resource_id = request.json.get("resource_id")
    actions = request.json.get("actions", ["all"])
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    if "all" in actions:
        if not await ActionPermissions.filter(tool_name=name, action_name="all", deleted=False, tenant_id=tenant_id).exists():
            await ActionPermissions.create(
                tool_name=name,
                action_name="all",
                action_description="动作全部动作权限",
                roles=[],
                users=[],
                tenant_id=tenant_id,
            )
        permissions = await ActionPermissions.filter(tool_name=name, deleted=False, tenant_id=tenant_id).all()
    else:
        permissions = await ActionPermissions.filter(tool_name=name, action_name__in=actions, deleted=False, tenant_id=tenant_id).all()

    if not permissions:
        raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)

    permission_list = []
    for permission in permissions:
        users_data = []
        if isinstance(permission.users, list):
            users_data = permission.users
        elif isinstance(permission.users, str):
            try:
                users_data = orjson.loads(permission.users)
            except orjson.JSONDecodeError:
                users_data = []

        user_ids = []
        if users_data and isinstance(users_data[0], dict):
            user_ids = users_data[0].get(resource_id, [])

        roles_data = []
        if isinstance(permission.roles, list):
            roles_data = permission.roles
        elif isinstance(permission.roles, str):
            try:
                roles_data = orjson.loads(permission.roles)
            except orjson.JSONDecodeError:
                roles_data = []

        role_ids = []
        if roles_data and isinstance(roles_data[0], dict):
            role_ids = roles_data[0].get(resource_id, [])

        permission_list.append(
            {
                "name": permission.action_name,
                "description": permission.action_description,
                "users_id": user_ids,
                "roles_id": role_ids,
            }
        )

    return response_ok(permission_list)


@tools_bp.post("/action/permissions/judge")
@openapi.definition(
    summary="多资源权限判断接口",
    description="多资源权限判断接口 (适配列表字典结构)",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["actions"],
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["tool_name", "resource_id", "actions"],
                            "properties": {
                                "tool_name": {
                                    "type": "string",
                                    "description": "工具名称",
                                },
                                "resource_id": {
                                    "type": "string",
                                    "description": "资源ID",
                                },
                                "actions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "动作列表",
                                },
                            },
                        },
                        "description": "工具-资源-动作列表",
                    }
                },
            }
        }
    },
    response={
        "200": {
            "description": "判断成功",
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
                                        "tool_name": {"type": "string"},
                                        "resource_id": {"type": "string"},
                                        "permissions": {
                                            "type": "object",
                                            "additionalProperties": {"type": "boolean"},
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
@requires_permission("tool:r")
async def judge_permission(request):
    action_groups = request.json.get("actions", [])
    if not action_groups or not isinstance(action_groups, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    user = request.ctx.session.get("account_info")
    if not user:
        raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)

    user_id = str(user.id)
    role_ids = {str(role.id) for role in user.roles}

    query_conditions = []
    for group in action_groups:
        tool_name = group.get("tool_name")
        resource_id = group.get("resource_id")
        actions = group.get("actions", [])

        if not all([tool_name, resource_id, isinstance(actions, list)]):
            continue

        query_conditions.append(Q(tool_name=tool_name, action_name__in=actions))

    permissions = await ActionPermissions.filter(reduce(operator.or_, query_conditions), deleted=False, tenant_id=tenant_id)

    perm_dict = defaultdict(dict)
    for p in permissions:
        perm_dict[(p.tool_name, p.action_name)] = p

    result = []
    for group in action_groups:
        tool_name = group["tool_name"]
        resource_id = group["resource_id"]
        action_results = {}

        for action in group["actions"]:
            permission = perm_dict.get((tool_name, action))

            if not permission:
                action_results[action] = False
                continue

            user_allowed = False
            if permission.users:
                for res_dict in permission.users:
                    if resource_id in res_dict:
                        user_allowed = user_id in map(str, res_dict[resource_id])
                        if user_allowed:
                            break

            role_allowed = False
            if permission.roles:
                for res_dict in permission.roles:
                    if resource_id in res_dict:
                        role_allowed = not role_ids.isdisjoint(map(str, res_dict[resource_id]))
                        if role_allowed:
                            break

            action_results[action] = user_allowed or role_allowed

        result.append(
            {
                "tool_name": tool_name,
                "resource_id": resource_id,
                "permissions": action_results,
            }
        )

    return response_ok(result)


@tools_bp.post("/tag/add")
@openapi.definition(
    summary="新增工具标签",
    description="新增工具标签（需要工具更新权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "tags"],
                "properties": {
                    "name": {"type": "string", "description": "工具名称"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签名称",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "新增成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:u")
async def add_tag(request):
    name = request.json.get("name")
    tags = request.json.get("tags", [])
    if not name or not tags:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    tools = await Tool.filter(name=name, deleted=False, tenant_id=tenant_id).all()

    if not tools:
        raise CustomError(ErrorCode.TOOL_NOT_FOUND_ERROR)

    for tool in tools:
        tool.tag = list(set(tool.tag or []) | set(tags))
        tool.updater = request.ctx.session["account_info"].username
        await tool.save()

    return response_ok({"message": "工具标签新增成功"})


@tools_bp.post("/tag/delete")
@openapi.definition(
    summary="删除工具标签",
    description="删除工具标签（需要工具更新权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name", "tags"],
                "properties": {
                    "name": {"type": "string", "description": "工具名称"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签名称",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:u")
async def delete_tag(request):
    name = request.json.get("name")
    tags = request.json.get("tags", [])
    if not name or not tags:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    tools = await Tool.filter(name=name, deleted=False, tenant_id=tenant_id).all()

    if not tools:
        raise CustomError(ErrorCode.TOOL_NOT_FOUND_ERROR)

    for tool in tools:
        tool.tag = list(set(tool.tag or []) - set(tags))
        tool.updater = request.ctx.session["account_info"].username
        await tool.save()

    return response_ok({"message": "工具标签删除成功"})


@tools_bp.post("/health/check/config")
@openapi.definition(
    summary="修改工具健康检查配置信息",
    description="修改工具健康检查配置信息（需要工具更新权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["switch"],
                "properties": {
                    "switch": {"type": "string", "description": "开关 on/off"},
                    "cron": {
                        "type": "string",
                        "description": "定时任务 格式：分 时 日 月 周，如：0 48 14 ? * 1 表示每周一的14点48分执行一次(多个小时段表示用逗号分隔，周也是如此)",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "修改成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:c")
async def health_check_config(request):
    switch = request.json.get("switch")
    cron = request.json.get("cron")

    # 参数验证
    if switch == "on":
        if not cron:
            raise CustomError(ErrorCode.PARAM_ERROR)
        await Config.update_or_create(key="health_check_cron", defaults={"value": cron}, module="tool")

    await Config.update_or_create(key="health_check_switch", defaults={"value": switch}, module="tool")

    return response_ok({"message": "资源健康检查配置修改成功"})


@tools_bp.post("/health/check/get")
@openapi.definition(
    summary="获取健康检测配置信息",
    description="获取健康检测配置信息（需要工具查看权限）",
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
                                    "switch": {"type": "string"},
                                    "cron": {"type": "string"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:r")
async def health_check_get(request):
    switch = await Config.get_or_none(key="health_check_switch")
    cron = await Config.get_or_none(key="health_check_cron")
    return response_ok(
        {
            "switch": switch.value if switch else "off",
            "cron": cron.value if cron else "",
        }
    )


@tools_bp.post("/action/input")
@openapi.definition(
    summary="获取工具动作输入参数",
    description="获取工具动作输入参数（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer", "description": "动作ID"}},
            }
        }
    },
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
                                        "key": {"type": "string"},
                                        "type": {"type": "string"},
                                        "required": {"type": "boolean"},
                                        "description": {"type": "string"},
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
@requires_permission("tool:r")
async def tool_action_input(request):
    action_id = request.json.get("id")
    if not action_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    action = await ToolAction.get_or_none(id=action_id, deleted=False, tenant_id=tenant_id)
    if not action:
        raise CustomError(ErrorCode.ACTION_NOT_FOUND_ERROR)

    input_data = action.action["input"]

    return response_ok(input_data)


async def tool_action_update(data, tenant_id):
    if not isinstance(data, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)

    affected_tools = set()

    for playbook_version_id, action_ids in data.items():
        for action_id in action_ids:
            action = await ToolAction.get_or_none(id=action_id, deleted=False, tenant_id=tenant_id)
            if not action:
                continue

            if not isinstance(action.playbook, list):
                action.playbook = []

            if playbook_version_id not in action.playbook:
                action.playbook.append(playbook_version_id)
                await action.save()

                affected_tools.add((action.tool_name, action.version_number))

    for tool_name, version in affected_tools:
        tool = await Tool.get_or_none(name=tool_name, version=version, deleted=False, tenant_id=tenant_id)
        if tool and tool.status != 1:
            tool.status = 1
            await tool.save()


async def tool_action_playbook_delete(data, tenant_id):
    """
    删除动作绑定的剧本
        - data   动作数据    dict
            {
                "剧本版本id": [
                    "动作a id",
                    "动作b id"
                ]
            }
    """
    if not isinstance(data, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)

    affected_tools = set()

    for playbook_id, action_ids in data.items():
        for action_id in action_ids:
            action = await ToolAction.get_or_none(id=action_id, deleted=False, tenant_id=tenant_id)
            if not action:
                continue
            if not isinstance(action.playbook, list):
                action.playbook = []

            if playbook_id in action.playbook:
                action.playbook.remove(playbook_id)
                await action.save()
            affected_tools.add((action.tool_name, action.version_number))

    for tool_name, version in affected_tools:
        actions = await ToolAction.filter(tool_name=tool_name, version_number=version, deleted=False, tenant_id=tenant_id).all()
        has_playbook = any(action.playbook for action in actions)
        tool = await Tool.get_or_none(name=tool_name, version=version, deleted=False, tenant_id=tenant_id)
        if tool:
            tool.status = 1 if has_playbook else 0
            await tool.save()


@tools_bp.post("/tag/new")
@openapi.definition(
    summary="新建标签",
    description="新建标签（需要工具新增权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "description": "标签名称"}},
            }
        }
    },
    response={
        "200": {
            "description": "新建成功",
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
@requires_permission("tool:c")
async def new_tag(request):
    name = request.json.get("name", "").strip()
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    if await Tag.filter(name=name, type=1, tenant_id=tenant_id).exists():
        raise CustomError(ErrorCode.TAG_EXIST_ERROR)

    await Tag.create(name=name, type=1, tenant_id=tenant_id)

    return response_ok("标签新建成功")


@tools_bp.post("/tag/list")
@openapi.definition(
    summary="获取标签列表",
    description="获取标签列表（需要工具查看权限）",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "array", "items": {"type": "string"}},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:r")
async def tag_list(request):
    tenant_id = request.ctx.session.get("tenant_id")

    tags = await Tag.filter(type=1, deleted=False, tenant_id=tenant_id).all()

    return response_ok([tag.name for tag in tags])


@tools_bp.post("/tag/remove")
@openapi.definition(
    summary="删除标签",
    description="删除标签（需要工具删除权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "description": "标签名称"}},
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
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
@requires_permission("tool:d")
async def remove_tag(request):
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    tag = await Tag.get(name=name, type=1, tenant_id=tenant_id)
    await tag.delete()

    return response_ok("标签删除成功")


@tools_bp.post("/engine/list")
@openapi.definition(
    summary="获取执行引擎列表",
    description="获取执行引擎列表（需要工具查看权限）",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "array", "items": {"type": "string"}},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:r")
async def engine_list(request):
    c = caches.get("redis_action").client
    lion_keys = []
    async for key in c.scan_iter(match="agent_*", count=100):
        lion_keys.append(key.decode().replace("agent_", ""))

    return response_ok(lion_keys)


@tools_bp.post("/health-check")
@openapi.definition(
    summary="进行资源健康检查",
    description="进行资源健康检查（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "action_input": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "description": "类型 flow/action",
                            },
                            "app_name": {
                                "type": "string",
                                "description": "应用名称，英文标识",
                            },
                            "name": {"type": "string", "description": "应用中文名称"},
                            "app_ver": {"type": "string", "description": "应用版本号"},
                            "main": {"type": "string", "description": "主程序文件名"},
                            "action_input": {
                                "type": "object",
                                "description": "动作输入参数",
                            },
                            "node_name": {"type": "string", "description": "节点名称"},
                            "action": {
                                "type": "string",
                                "description": "动作名称 英文标识",
                            },
                            "action_name": {
                                "type": "string",
                                "description": "动作名称 中文名称",
                            },
                            "retry_times": {
                                "type": "integer",
                                "description": "重试次数",
                            },
                            "timeout": {
                                "type": "integer",
                                "description": "超时时间,单位秒",
                            },
                            "resource": {
                                "type": "string",
                                "description": "进行资源检查的id",
                            },
                        },
                    }
                },
            }
        }
    },
    response={"200": {"description": "健康检查执行成功"}},
)
@requires_permission("tool:r")
async def health_check(request):
    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    utime = datetime.datetime.now(TZ)
    message = request.json.get("action_input", {})
    if message.get("type") in ("flow", "action"):
        run_id = gen_run_id()
        message["executor"] = request.ctx.session["account_info"].id
        msg = {"run_type": message["type"], "run_id": run_id, "run_args": message, "tenant_id": request.ctx.session.get("tenant_id")}
        await aproduce(TASK_TOPIC, msg)
        sent_id = []
        for _ in range(60):
            log = await Runlog.get_or_none(run_id=run_id, utime__gte=utime)
            if log:
                if log.id not in sent_id:
                    sent_id.append(log.id)
                    await resp.send(f"data: {orjson.dumps(log.json).decode()}\n\n")
                if log.result != "执行中":
                    break
            await asyncio.sleep(1)

    if resp:
        await resp.eof()

    return resp


@tools_bp.post("/debug")
@openapi.definition(
    summary="debug工具",
    description="debug工具 用于工具开发时调试使用（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["data"],
                "properties": {"data": {"type": "string", "description": "python脚本代码"}},
            }
        }
    },
    response={"200": {"description": "调试执行成功"}},
)
@requires_permission("tool:r")
async def tool_debug(request):
    data = request.json.get("data")
    if not data:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    tool = await Tool.filter(name="lion_mgmt", deleted=False).order_by("-ctime").first()
    if not tool:
        return response_ok("工具开发运行APP缺失，请联系管理员处理")

    resource = await ToolResources.filter(tool_name="lion_mgmt", deleted=False).order_by("-ctime").first()
    if not resource:
        return response_ok("工具开发运行资源不存在，请联系管理员处理")

    user = request.ctx.session["account_info"]
    params = {
        "resource": resource.id,
        "node_name": tool.description,
        "action": "run_python_script",
        "action_name": "执行python脚本",
        "app_name": "lion_mgmt",
        "name": tool.description,
        "app_ver": tool.version,
        "main": tool.main,
        "action_input": {"data": data},
        "retry_times": 0,
        "timeout": 300,
        "executor": user.id,
    }
    run_id = gen_run_id()
    msg = {"run_type": "action", "run_id": run_id, "run_args": params, "tenant_id": tenant_id}
    await aproduce(TASK_TOPIC, msg)

    for _ in range(30):
        await asyncio.sleep(1)
        log = await Runlog.get_or_none(run_id=run_id)
        if log and log.result and log.result != "执行中":
            result = orjson.loads(log.result)
            data = result.get("data")
            return response_ok(data)

    raise CustomError(ErrorCode.ACTION_TIMEOUT)


@tools_bp.post("/add-config")
@openapi.definition(
    summary="添加config配置",
    description="添加config配置",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["key", "value", "module"],
                "properties": {
                    "key": {"type": "string", "description": "键"},
                    "value": {"type": "string", "description": "值"},
                    "module": {"type": "string", "description": "所属模块"},
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
@requires_permission("tool:u")
async def add_config(request):
    data = request.json
    await Config.update_or_create(key=data["key"], module=data["module"], defaults={"value": data["value"]})
    return response_ok("添加成功")


@tools_bp.post("/health-check-result")
@openapi.definition(
    summary="获取健康检查结果",
    description="获取健康检查结果（需要工具更新权限）",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "object"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("tool:u")
async def get_health_check_result(request):
    result = await Config.get_or_none(key="health_check", module="tools")
    data = {}
    if result:
        data = result.json
        data["value"] = result.json_value
    return response_ok(data)


@tools_bp.post("/resource-search")
@openapi.definition(
    summary="资源搜索接口",
    description="根据资源名称模糊搜索资源（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "资源名称"},
                },
            }
        }
    },
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
                                        "resource_name": {"type": "string"},
                                        "tool_name": {"type": "string"},
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
@requires_permission("tool:r")
async def resource_search(request):
    """
    模糊搜索资源名称，并在返回结果中带上对应的工具英文名
    """
    keyword = request.json.get("keyword", "")
    if not keyword:
        return response_ok([])

    resources = await ToolResources.filter(name__icontains=keyword, deleted=False, internal=False).all()
    result = []
    for res in resources:
        result.append(res.json)
    return response_ok(result)


@tools_bp.post("/actions/available")
@openapi.definition(
    summary="获取所有可用动作列表",
    description="获取所有可用动作列表（可用定义为：存在资源或不需要资源的动作），需要工具查看权限",
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
                                        "id": {"type": "string"},
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "tool_name": {"type": "string"},
                                        "version": {"type": "string"},
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
@requires_permission("tool:r")
async def get_available_actions(request):
    """
    获取所有可用动作列表
    可用定义为：
    1. 无需资源（status=0 或不存在资源模型）
    2. 存在资源（status=1 且 ToolResources 中存在该工具的资源）
    且工具的maintain字段为False
    """
    tenant_id = request.ctx.session.get("tenant_id")

    # 获取所有 maintain=False 的工具名称
    available_tool_names = await Tool.filter(
        deleted=False,
        maintain=False,
        tenant_id=tenant_id
    ).values_list("name", flat=True)

    # 获取所有工具资源模型
    tool_resources = await ToolResourcesModel.filter(deleted=False, tenant_id=tenant_id).all()

    # 构建工具名称到资源状态的映射
    tool_resource_status = {tr.tool_name: tr.status for tr in tool_resources}

    # 获取所有实际存在的资源（用于判断status=1的工具是否有资源）
    actual_resources = await ToolResources.filter(
        deleted=False,
        tenant_id=tenant_id,
        tool_name__in=available_tool_names
    ).values_list("tool_name", flat=True)
    tools_with_resources = set(actual_resources)

    # 获取这些工具的动作（只查询 maintain=False 的工具）
    actions = await ToolAction.filter(
        deleted=False,
        tenant_id=tenant_id,
        tool_name__in=available_tool_names
    ).all()

    result = []
    for action in actions:
        # 检查该动作的工具是否可用
        # 可用条件：
        # 1. 工具没有资源模型（不需要资源）
        # 2. 工具资源模型的status为0（无需资源或资源已配置）
        # 3. 工具资源模型的status为1 且 ToolResources中存在该工具的资源
        tool_status = tool_resource_status.get(action.tool_name)

        if tool_status is None or tool_status == 0:
            # 没有资源模型或status=0，认为可用
            action_data = action.action
            result.append({
                "id": str(action.id),
                "name": action_data.get("name", ""),
                "description": action_data.get("name", ""),
                "tool_name": action.tool_name,
                "version": action.version_number,
            })
        elif tool_status == 1 and action.tool_name in tools_with_resources:
            # status=1 且存在实际资源，认为可用
            action_data = action.action
            result.append({
                "id": str(action.id),
                "name": action_data.get("name", ""),
                "description": action_data.get("name", ""),
                "tool_name": action.tool_name,
                "version": action.version_number,
            })

    return response_ok(result)


@tools_bp.post("/resource/sync-health-status")
@openapi.definition(
    summary="同步资源健康状态",
    description="从Config表中获取健康检查数据并更新ToolResources的status字段（需要工具更新权限）",
    response={
        "200": {
            "description": "同步成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                    "updated_count": {"type": "integer"},
                                    "details": {
                                        "type": "object",
                                        "properties": {
                                            "available": {"type": "integer"},
                                            "unavailable": {"type": "integer"},
                                            "unknown": {"type": "integer"},
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
@requires_permission("tool:u")
async def sync_health_status(request):
    """
    从Config表中获取健康检查数据并更新ToolResources的status字段
    """
    tenant_id = request.ctx.session.get("tenant_id")

    # 从Config表获取健康检查数据
    health_check_config = await Config.get_or_none(key="health_check", module="tools")
    if not health_check_config:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 解析健康检查数据
    try:
        health_data = orjson.loads(health_check_config.value)
    except Exception:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 统计更新数量
    updated_count = 0
    status_counts = {
        "available": 0,
        "unavailable": 0,
        "unknown": 0,
    }

    # 处理 available 列表
    for item in health_data.get("available", []):
        tool_name = item.get("tool_name")
        resource_name = item.get("resource")
        state = item.get("state", "available")

        if tool_name and resource_name:
            resource = await ToolResources.get_or_none(
                tool_name=tool_name,
                name=resource_name,
                deleted=False,
                tenant_id=tenant_id,
                internal=False
            )
            if resource:
                resource.status = state
                await resource.save(update_fields=["status"])
                updated_count += 1
                status_counts["available"] += 1

    # 处理 unavailable 列表
    for item in health_data.get("unavailable", []):
        tool_name = item.get("tool_name")
        resource_name = item.get("resource")
        state = item.get("state", "unavailable")

        if tool_name and resource_name:
            resource = await ToolResources.get_or_none(
                tool_name=tool_name,
                name=resource_name,
                deleted=False,
                tenant_id=tenant_id,
                internal=False
            )
            if resource:
                resource.status = state
                await resource.save(update_fields=["status"])
                updated_count += 1
                status_counts["unavailable"] += 1

    # 处理 no_check_action 列表
    for item in health_data.get("no_check_action", []):
        tool_name = item.get("tool_name")
        resource_name = item.get("resource")
        state = item.get("state", "unknown")

        if tool_name and resource_name:
            resource = await ToolResources.get_or_none(
                tool_name=tool_name,
                name=resource_name,
                deleted=False,
                tenant_id=tenant_id,
                internal=False
            )
            if resource:
                resource.status = state
                await resource.save(update_fields=["status"])
                updated_count += 1
                status_counts["unknown"] += 1

    return response_ok({
        "message": "资源健康状态同步成功",
        "updated_count": updated_count,
        "details": status_counts,
    })


class SSEStreamingHook(AgentHook):
    """SSE 流式输出 Hook - 将 Nanobot 流式输出通过 SSE 发送"""

    def __init__(self, sse_response, workspace_path: Path):
        """
        初始化 SSE 流式 Hook

        Args:
            sse_response: Sanic SSE 响应对象
            workspace_path: nanobot workspace 路径，用于检测截图文件
        """
        self.sse_response = sse_response
        self.workspace_path = workspace_path
        self._has_content = False
        self._screenshot_files = set()  # 记录已发送的截图文件
        self._accumulated_content = ""  # 累积内容，用于检测代码 JSON

    def wants_streaming(self) -> bool:
        return True

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        """流式输出时通过 SSE 发送"""
        self._has_content = True
        self._accumulated_content += delta

        try:
            message = orjson.dumps({"status": "message", "content": delta}).decode()
            await self.sse_response.send(f"data: {message}\n\n")
        except Exception as e:
            logger.error(f"[SSEStreamingHook] 发送流式消息失败: {e}")

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        """流式输出结束时检测并提取代码 JSON"""
        # 尝试从累积内容中提取代码 JSON
        await self._extract_and_send_code_json()

    async def _extract_and_send_code_json(self):
        """从累积内容中提取代码 JSON 并发送"""
        try:
            # 查找 JSON 代码块（```json ... ```）
            json_pattern = r'```json\s*\n(.*?)\n```'
            matches = re.findall(json_pattern, self._accumulated_content, re.DOTALL)

            if matches:
                # 取最后一个匹配（最新的代码）
                json_str = matches[-1].strip()

                # 尝试解析 JSON
                try:
                    code_data = orjson.loads(json_str)

                    # 检查是否包含 config 和 main 字段
                    if "config" in code_data and "main" in code_data:
                        # 发送代码 JSON
                        message = orjson.dumps({
                            "status": "code",
                            "data": code_data
                        }).decode()
                        await self.sse_response.send(f"data: {message}\n\n")
                        logger.info("[SSEStreamingHook] 已发送代码 JSON")
                except orjson.JSONDecodeError as e:
                    logger.warning(f"[SSEStreamingHook] 解析代码 JSON 失败: {e}")
        except Exception as e:
            logger.error(f"[SSEStreamingHook] 提取代码 JSON 失败: {e}")

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        """在执行工具前发送工具调用信息"""
        if not context.tool_calls:
            return

        for tool_call in context.tool_calls:
            tool_name = tool_call.name
            try:
                # 发送工具执行状态
                message = orjson.dumps({
                    "status": "executing",
                    "message": f"正在执行 {tool_name}..."
                }).decode()
                await self.sse_response.send(f"data: {message}\n\n")
            except Exception as e:
                logger.error(f"[SSEStreamingHook] 发送工具调用消息失败: {e}")

    async def after_iteration(self, context: AgentHookContext) -> None:
        """在每次迭代后检查截图文件并发送"""
        # 检查 workspace 中的截图文件
        try:
            screenshot_patterns = ["*.png", "*.jpg", "*.jpeg"]
            for pattern in screenshot_patterns:
                for screenshot_path in self.workspace_path.glob(f"**/{pattern}"):
                    # 只发送新的截图文件
                    if screenshot_path not in self._screenshot_files:
                        self._screenshot_files.add(screenshot_path)
                        await self._send_screenshot(screenshot_path)
        except Exception as e:
            logger.error(f"[SSEStreamingHook] 检查截图文件失败: {e}")

        # 如果有错误，发送错误消息
        if context.error:
            try:
                if context.tool_calls:
                    error_msg = "工具执行失败"
                else:
                    error_msg = "AI 调用失败，请稍后重试"

                message = orjson.dumps({
                    "status": "error",
                    "message": error_msg
                }).decode()
                await self.sse_response.send(f"data: {message}\n\n")
            except Exception as e:
                logger.error(f"[SSEStreamingHook] 发送错误消息失败: {e}")

    async def _send_screenshot(self, screenshot_path: Path):
        """发送截图文件（base64 编码）"""
        try:
            with open(screenshot_path, "rb") as f:
                screenshot_data = base64.b64encode(f.read()).decode()

            message = orjson.dumps({
                "status": "screenshot",
                "data": screenshot_data,
                "filename": screenshot_path.name
            }).decode()
            await self.sse_response.send(f"data: {message}\n\n")
            logger.info(f"[SSEStreamingHook] 已发送截图: {screenshot_path.name}")
        except Exception as e:
            logger.error(f"[SSEStreamingHook] 发送截图失败: {e}")


@tools_bp.post("/browser/execute")
@openapi.definition(
    summary="执行浏览器自动化任务",
    description="通过 nanobot 自然语言调用 agent-browser skill 执行浏览器自动化任务，支持会话上下文和截图返回（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["task"],
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "浏览器自动化任务描述（自然语言）",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "会话 ID（用于保持 nanobot 上下文历史，可选）",
                    },
                },
            }
        }
    },
    response={"200": {"description": "执行成功，返回 SSE 流（包含文本消息、截图数据和生成的代码）"}},
)
@requires_permission("tool:r")
async def browser_execute(request):
    """
    通过 nanobot 执行浏览器自动化任务

    工作流程：
    1. 接收自然语言任务描述
    2. 如果提供 session_id，加载历史上下文
    3. 调用 nanobot 处理任务
    4. nanobot 自动调用 agent-browser skill
    5. 实时返回执行进度、结果和截图

    SSE 事件类型：
    - status: starting - 开始处理任务
    - status: processing - nanobot 正在理解任务
    - status: executing - 正在执行工具（如 agent-browser）
    - status: message - 文本消息流式输出
    - status: screenshot - 截图数据（base64 编码）
    - status: code - 生成的代码（JSON 格式：{"config": "...", "main": "..."}）
    - status: completed - 任务执行完成
    - status: error - 执行出错
    """
    resp = None
    session_id = None

    try:
        # 初始化 SSE 响应
        resp = await request.respond(
            content_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

        # 检查请求体是否有效
        if not request.json:
            error_data = orjson.dumps({"status": "error", "message": "请求体必须是有效的 JSON"}).decode()
            await resp.send(f"data: {error_data}\n\n")
            await resp.eof()
            return resp

        task = request.json.get("task")
        session_id = request.json.get("session_id")

        if not task:
            error_data = orjson.dumps({"status": "error", "message": "task 参数不能为空"}).decode()
            await resp.send(f"data: {error_data}\n\n")
            await resp.eof()
            return resp

        # 如果没有提供 session_id，自动生成一个
        if not session_id:
            session_id = str(uuid.uuid4())
            logger.info(f"未提供 session_id，自动生成: {session_id}")

        tenant_id = request.ctx.session.get("tenant_id")

        # 返回 session_id 给客户端，用于后续请求
        start_data = orjson.dumps({
            "status": "starting",
            "message": "开始处理浏览器自动化任务...",
            "session_id": session_id
        }).decode()
        await resp.send(f"data: {start_data}\n\n")

        # 1. 初始化 NanobotClient
        logger.info(f"初始化 NanobotClient，tenant_id={tenant_id}, session_id={session_id}")
        client = NanobotClient(tenant_id=tenant_id, workspace_base=NANOBOT_WORKSPACE_BASE)

        # 获取 workspace 路径用于检测截图
        workspace_path = Path(NANOBOT_WORKSPACE_BASE) / tenant_id

        # 2. 创建 SSE 流式 Hook
        hook = SSEStreamingHook(sse_response=resp, workspace_path=workspace_path)

        # 3. 构建完整消息：系统指令 + 用户任务
        full_message = f"""<system_instruction>
【身份】你是浏览器自动化助手 nanobot，专门帮助用户完成网页操作任务。

【完整工作流程】
这是一个迭代式的浏览器自动化 + 代码生成流程：
1. 用户输入自然语言任务描述
2. 你使用 agent-browser skill 执行任务
3. 返回执行结果（包括截图）给用户确认
4. 用户查看结果后有两种情况：
   a) 结果有问题 → 用户调用本接口输入需要校正的地方 → 回到步骤 2
   b) 结果正确 → 用户调用本接口要求生成代码 → 你根据 aioe-app-dev skill 规范生成执行过程的 Python 代码
5. 完成整个流程

【任务识别】
根据用户输入判断当前是哪个阶段：
- 如果用户描述的是浏览器操作任务（如"访问某网站"、"登录"、"截图"等）→ 调用 agent-browser skill 执行
- 如果用户提到"校正"、"调整"、"修改"、"重新"等词 → 这是校正请求，根据反馈调整并重新执行 agent-browser
- 如果用户要求"生成代码"、"输出代码"、"转换成代码"等 → 根据之前的执行历史，使用 aioe-app-dev skill 生成 Python 代码

【执行浏览器任务的规则】
1. 必须立即调用 agent-browser skill，严禁仅用文字回复
2. 理解自然语言任务意图并转换为具体的浏览器操作
3. 每次执行完成后，必须提供截图作为结果证明
4. 如果是校正请求，需要：
   - 理解用户的反馈和期望
   - 调整浏览器操作策略
   - 重新执行任务
   - 确保这次的结果符合用户的要求

【生成代码的规则 - 重要】
1. 当用户要求生成代码时，回顾本次会话中的所有浏览器操作历史
2. 根据 aioe-app-dev skill 的规范（AIOE 应用动作代码生成规范）生成 Python 代码
3. 代码必须包含：
   - 文件头部（# coding=utf-8 和文档字符串）
   - health_check 函数
   - 主要业务函数（根据任务命名，使用 snake_case）
   - 完整的异常处理（try-except）
   - 从 assets 获取配置参数
   - 统一的返回格式（{{"code": 0/-1, "msg": "", "data": ...}}）
4. 如果任务使用了 agent-browser，使用 agent-browser 操作模板
5. 同时生成配套的 config.json 文件

【代码返回格式 - 必须遵守】
生成代码后，**不要使用 write_file 工具保存文件**，而是直接在回复中以 JSON 格式返回：

```json
{{
    "config": "config.json 的完整内容（JSON 字符串）",
    "main": "main.py 的完整内容（Python 代码字符串，保留 \\n 换行符）"
}}
```

示例：
```json
{{
    "config": "{{\\"name\\": \\"时间戳工具\\", \\"main\\": \\"main.py\\", ...}}",
    "main": "# coding=utf-8\\n\\"\\"\\"\\nAIOE 应用动作代码\\n\\"\\"\\"\\n\\nimport subprocess\\n..."
}}
```

【重要提醒】
- 不要问用户"是否需要我调用 skill"，直接调用！
- 不要说"我可以帮你..."，直接做！
- 执行浏览器任务时，不要只用文字描述，必须调用 agent-browser skill！
- 生成代码时，必须严格遵循 aioe-app-dev skill 的规范和模板
- **生成代码时，绝对不要使用 write_file 工具，必须以 JSON 格式返回代码内容**
- 如果 skill 调用失败，告知用户具体的错误信息

【上下文保持】
- 本次对话会保持会话上下文，你可以看到之前的执行历史
- 利用历史信息理解用户的校正意图或代码生成需求
- 生成代码时，要基于实际执行过的操作，不要凭空想象
</system_instruction>

<user_task>
{task}
</user_task>"""

        # 4. 发送处理状态
        processing_data = orjson.dumps({"status": "processing", "message": "nanobot 正在理解任务..."}).decode()
        await resp.send(f"data: {processing_data}\n\n")

        # 5. 调用 nanobot（使用 session_id 保持上下文）
        session_key = f"browser:{session_id}"

        logger.info(f"准备调用 nanobot，session_key={session_key}")
        logger.info(f"消息内容长度: {len(full_message)} 字符")

        result = await client.ask(
            message=full_message,
            streaming=True,
            session_key=session_key,
            hooks=[hook]
        )

        logger.info(f"Nanobot 调用完成，返回结果长度: {len(result) if result else 0} 字符")

        # 6. 返回完成状态
        completed_data = orjson.dumps({"status": "completed", "message": "任务执行完成"}).decode()
        await resp.send(f"data: {completed_data}\n\n")

    except Exception as e:
        logger.exception("浏览器自动化任务执行失败")
        try:
            if resp:
                # 使用 orjson 确保 JSON 安全
                error_data = orjson.dumps({
                    "status": "error",
                    "message": f"执行失败: {str(e)}",
                    "session_id": session_id  # 即使失败也返回 session_id
                }).decode()
                await resp.send(f"data: {error_data}\n\n")
        except Exception as send_error:
            logger.error(f"发送错误消息失败: {send_error}")

    finally:
        if resp:
            try:
                await resp.eof()
            except Exception as eof_error:
                logger.error(f"关闭 SSE 响应失败: {eof_error}")

    return resp

