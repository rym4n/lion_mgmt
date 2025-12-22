# coding=utf-8
import asyncio
import datetime
import hashlib
import operator
import os
import re
import shutil
import time
import zipfile
from collections import defaultdict
from functools import reduce
from io import BytesIO

import orjson
from aiocache import caches
from sanic import Blueprint
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.functions import Max
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs import utils
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission, load_license
from libs.utils import gen_run_id
from models import Tool, ToolAction, ToolResources, ToolResourcesModel, ActionPermissions, Config, Tag, PlaybookVersion, \
    Runlog, Timed
from settings import KAFKA_SERVERS, TASK_TOPIC, TZ, TOOLS_DIR

# 定义 Blueprint
tools_bp = Blueprint("tools", url_prefix="/tools")
__all__ = ["tools_bp", 'tool_action_update', 'tool_action_playbook_delete']
# 预期的文件结构
EXPECTED_STRUCTURE = {
    "required_files": ["config.json"]
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
                        "description": "ZIP文件"
                    }
                }
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
                                                "msg": {"type": "string"}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:c")
async def upload_and_extract_zip(request):
    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

    creator = request.ctx.session['account_info'].username
    if "file" not in request.files:
        return response_ok({"error": "No file provided"})

    results = []
    # 获取 ZIP 文件
    zip_files = request.files["file"]
    single_progress = 100 / len(zip_files)
    success_progress = 0
    data = 0
    total_up_value = 0

    lic = load_license()
    if not lic:
        raise CustomError(ErrorCode.InvalidLicenseError)

    for zip_file in zip_files:
        total_count = await Tool.filter(deleted=False).count()
        zip_filename = zip_file.name

        for key, item in lic["grouplist"].items():
            if key == "应用集成":
                if int(item["expireTime"]) > int(time.time() * 1000):
                    total_up_value = int(item["value"])

        if total_up_value != 99999 and total_count >= total_up_value:
            return response_ok({"error": "应用数量已达到上限"})

        config_data, version, tool_name, msg = await unzip(zip_file.body, creator, single_progress, resp,
                                                           success_progress, request.app.ctx.cache_client,
                                                           request.ctx.session["id"])
        if msg != "success":
            results.append({"tool": zip_filename, "msg": msg})
            data += 1
            percent = int((data * single_progress))
            success_progress = percent
            await resp.send(f"data: 解压进度：{percent}%\n\n")
            continue

        if "actions" in config_data.keys():
            action_key = "actions"
        else:
            results.append({"tool": zip_filename, "msg": "config文件内容错误，缺失actions字段"})
            data += 1
            percent = int((data * single_progress))
            success_progress = percent
            continue
        health_check = 0
        for action in config_data.get(action_key):
            if "health_check" == action.get("func"):
                health_check = 1
            await ToolAction.create(tool_name=config_data.get("name"), version_number=version,
                                    action=action)
            actions = await ActionPermissions.filter(tool_name=tool_name, deleted=False).values("action_name")
            action_names = {action["action_name"] for action in actions}

            if action["func"] not in action_names:
                await ActionPermissions.create(tool_name=tool_name, action_name=action["func"],
                                               action_description=action.get("name") or "", roles=[],
                                               users=[])
            await ActionPermissions.create(tool_name=tool_name, action_name="all",
                                           action_description="动作全部动作权限", roles=[],
                                           users=[])
        configuration = await ToolResourcesModel.get_or_none(tool_name=tool_name, deleted=False)
        if not configuration or configuration.resources != config_data.get("resource"):
            trm = await ToolResourcesModel.create(tool_name=tool_name, resources=config_data.get("resource") or {},
                                                  status=1 if (config_data.get("resource") and any(
                                                      field.get("required") is True for field in
                                                      config_data.get("resource").values())) else 0,
                                                  health_check=health_check)
            if health_check == 1:
                trm.version = version
                await trm.save()
        else:
            if configuration.health_check != 1 and health_check == 1:
                configuration.health_check = health_check
                configuration.version = version
                await configuration.save()

        results.append({"tool": zip_filename, "msg": msg})

        data += 1
        percent = int((data * single_progress))
        success_progress = percent
        await resp.send(f"data: 解压进度：{percent}%\n\n")

    if resp:
        await resp.send(f"data: {results}\n\n")
        await resp.eof()

    return resp


async def unzip(zip_data, creator, single_progress, resp, success_progress, redis_client, token_id):
    try:
        data = 0
        zip_buffer = BytesIO(zip_data)

        with zipfile.ZipFile(zip_buffer, 'r', metadata_encoding='utf-8') as zip_ref:
            version = None

            files_info = zip_ref.infolist()

            actual_files = {os.path.basename(f.filename) for f in files_info if not f.is_dir()}
            py_files_dict = {os.path.basename(f.filename): f for f in files_info if f.filename.endswith(".py")}
            config_path = next((f for f in files_info if os.path.basename(f.filename) == "config.json"), None)

            if config_path is None:
                return None, None, None, "config文件不存在"

            if config_path.filename.replace("\\", "/") != "config.json":
                return None, None, None, "config文件需要在最外层目录下"

            single_progress = single_progress / 10

            with zip_ref.open(config_path) as config_file:
                config_data = orjson.loads(config_file.read().decode())

            tool_name = config_data.get("name")
            pattern = r'^[A-Za-z][A-Za-z0-9_]*$'
            if not bool(re.match(pattern, tool_name)):
                return config_data, version, tool_name, "工具名称不合法"

            for info in files_info:
                if ".." in info.filename or info.filename.startswith("/"):
                    return config_data, version, tool_name, "文件名包含非法字符"

            if "description" not in config_data.keys():
                return config_data, version, tool_name, "config文件内容错误，缺失description字段"

            hash_func = getattr(hashlib, "md5", None)
            if hash_func is None:
                return config_data, version, tool_name, "Unsupported hash type. Use 'md5', 'sha1', 'sha256', or 'sha512'."
            data += 1
            percent = int(data * single_progress) + success_progress
            await resp.send(f"data: 解压进度：{percent}%\n\n")

            hasher = hash_func()
            zip_buffer.seek(0)  # 确保从头开始读取数据
            for chunk in iter(lambda: zip_buffer.read(4096), b""):
                hasher.update(chunk)

            if not config_path:
                return config_data, version, tool_name, "缺失config文件"
            data += 1
            percent = int(data * single_progress) + success_progress
            await resp.send(f"data: 解压进度：{percent}%\n\n")

            missing_files = set(EXPECTED_STRUCTURE["required_files"]) - actual_files
            if missing_files:
                return config_data, version, tool_name, "缺失config文件"
            data += 1
            percent = int(data * single_progress) + success_progress
            await resp.send(f"data: 解压进度：{percent}%\n\n")

            name = config_data.get("name")
            py_file_name = config_data.get("main")
            logo_name = config_data.get("logo")
            if "/" in logo_name:
                logo_name = logo_name.split("/")[-1]

            if "actions" in config_data.keys():
                action_key = "actions"
            else:
                return config_data, version, tool_name, "config文件内容错误，缺失actions字段"
            class_name_list = [action.get("func") for action in config_data.get(action_key, [])]

            for action in config_data.get(action_key, []):
                if not all(k in action for k in ["func", "name", "input"]):
                    return config_data, version, tool_name, "actions字段内容错误"

                inputs = action.get("input")
                if not isinstance(inputs, list):
                    return config_data, version, tool_name, "input字段内容错误"

                for input_item in inputs:
                    if not all(k in input_item for k in ["key", "type", "required", "description"]):
                        return config_data, version, tool_name, "input字段内容错误"

                    if any(input_item[k] in (None, "") for k in ["key", "type", "required", "description"]):
                        return config_data, version, tool_name, "input字段内容错误"

            if not py_file_name:
                return config_data, version, tool_name, "config文件内容错误"
            data += 1
            percent = int(data * single_progress) + success_progress
            await resp.send(f"data: 解压进度：{percent}%\n\n")

            if py_file_name not in py_files_dict:
                return config_data, version, tool_name, "缺失py文件"
            data += 1
            percent = int(data * single_progress) + success_progress
            await resp.send(f"data: 解压进度：{percent}%\n\n")

            if logo_name not in actual_files:
                return config_data, version, tool_name, "缺少logo文件"

            logo_found = False
            for file_info in files_info:
                if file_info.filename.lower().endswith((".png", ".jpg", ".jpeg", ".ico")):
                    logo_found = True
                    with zip_ref.open(file_info) as logo_file:
                        if not check_file_size(logo_file):
                            return config_data, version, tool_name, "logo文件大小超过5KB限制，请压缩后重新上传"
                        logo_data = logo_file.read()

            if not logo_found:
                return config_data, version, tool_name, "缺少logo文件"

            data += 1
            percent = int(data * single_progress) + success_progress
            await resp.send(f"data: 解压进度：{percent}%\n\n")

            with zip_ref.open(py_files_dict[py_file_name]) as py_file:
                py_data = py_file.read().decode()

            if any(f"def {class_name}(" not in py_data for class_name in class_name_list):
                return config_data, version, tool_name, "Python文件内容错误"
            data += 1
            percent = int(data * single_progress) + success_progress
            await resp.send(f"data: 解压进度：{percent}%\n\n")

            configuration = await ToolResourcesModel.filter(tool_name=name, deleted=False).first().values("resources")
            if configuration and configuration["resources"] != config_data.get("resource", {}):
                for key in config_data.get("resource", {}).keys():
                    if key not in configuration["resources"].keys() and config_data.get("resource", {})[key].get("required") is True:
                        return config_data, version, tool_name, "工具资源不一致，请新建工具或删除原工具后再次上传"
                    if key in configuration["resources"].keys() and config_data.get("resource", {})[key].get("data_type") != configuration["resources"][key].get("data_type"):
                        return config_data, version, tool_name, "工具资源不一致，请新建工具或删除原工具后再次上传"
            data += 1
            percent = int(data * single_progress) + success_progress
            await resp.send(f"data: 解压进度：{percent}%\n\n")

            hash_values = await Tool.filter(name=name, deleted=False).all().values("hash_value")
            hash_set = {entry["hash_value"] for entry in hash_values}
            if hash_values and hasher.hexdigest() in hash_set:
                return config_data, version, tool_name, "工具已存在"
            data += 1
            percent = int(data * single_progress) + success_progress
            await resp.send(f"data: 解压进度：{percent}%\n\n")

            version = "v" + str(int(time.time() * 1000))
            suffix = logo_name.split(".")[-1]
            save_path = await utils.upload_to_s3(zip_data, "system", f"{tool_name}/{version}.zip", "tools")
            logo_path = await utils.upload_to_s3(logo_data, "user", f"{tool_name}.{suffix}", "tools")

            maintenance_mode = await redis_client.get(token_id)
            tool = await Tool.create(
                name=name, version=version, path=save_path, logo_path=logo_path,
                description=config_data.get("description"),
                creator=creator, updater=creator,
                hash_value=hasher.hexdigest(),
                internal=config_data.get("internal", False),
                main=py_file_name,
                maintain=maintenance_mode if maintenance_mode else False
            )

            # 将config数据存入redis
            await redis_client.set(name + "_" + version, config_data, 2592000)

            return config_data, version, tool.name, "success"
    except zipfile.BadZipFile as e:
        return config_data, version, tool_name, str(e)


@tools_bp.post("/all")
@openapi.definition(
    summary="获取所有工具列表",
    description="获取所有工具列表（仅显示每个工具的最新版本，并对工具名称进行模糊搜索），需要工具查看权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "description": "页码（默认1）"
                    },
                    "size": {
                        "type": "integer",
                        "description": "每页数量（默认10）"
                    },
                    "key": {
                        "type": "string",
                        "description": "名称和描述的搜索（可选，模糊搜索）"
                    },
                    "tag": {
                        "type": "string",
                        "description": "标签名称（可选，精确搜索）"
                    },
                    "logo": {
                        "type": "integer",
                        "description": "是否需要logo数据 0为不需要，1为需要"
                    }
                }
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
                                                "health_check_version": {"type": "string"}
                                            }
                                        }
                                    },
                                    "total": {"type": "integer"},
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total_pages": {"type": "integer"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def all_tools(request):
    req_json = request.json or {}
    page = int(req_json.get("page", 1))
    size = int(req_json.get("size", 15))
    key = req_json.get("key", "").strip()

    if size > 50:
        raise CustomError(ErrorCode.RequestTooManyError)

    subquery = Tool.filter(deleted=False).annotate(max_version=Max("version")).group_by("name")
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    subquery = subquery.filter(maintain=maintenance_mode if maintenance_mode else False)
    if key:
        tool_actions = await ToolAction.filter(deleted=False).values("tool_name", "version_number", "action")
        tool_name_version_set = set()

        for act in tool_actions:
            act_data = act["action"]
            if isinstance(act_data, dict) and (
                    key.lower() in act_data.get("func", "").lower() or key.lower() in act_data.get("name", "").lower()
            ):
                tool_name_version_set.add((act["tool_name"], act["version_number"]))

        cond = Q(name__icontains=key) | Q(description__icontains=key)
        if tool_name_version_set:
            cond |= Q()
            for name, version in tool_name_version_set:
                cond |= Q(name=name, version=version)
        subquery = subquery.filter(cond)
    if req_json.get("tag"):
        subquery = subquery.filter(tag__contains=[req_json.get("tag")])
    subquery_results = await subquery.values("name", "max_version")
    if not subquery_results:
        return response_ok({
            "tools": [],
            "total": 0,
            "page": page,
            "size": size,
            "total_pages": 0
        })
    condition = Q()
    for pair in subquery_results:
        condition |= Q(name=pair["name"], version=pair["max_version"])

    latest_tools = await Tool.filter(condition) \
        .offset((page - 1) * size) \
        .limit(size) \
        .values()
    for tool in latest_tools:
        if int(req_json.get("logo", 1)) == 1 and tool["logo_path"]:
            tool["logo"] = tool["logo_path"]
        tool.pop("path", None)
        trm = await ToolResourcesModel.get_or_none(deleted=False, tool_name=tool["name"])
        if trm:
            tool["required"] = trm.status
            tool["health_check"] = trm.health_check
            tool["health_check_version"] = trm.version

    total_count = await Tool.filter(condition).count()

    return response_ok({
        "tools": latest_tools,
        "total": total_count,
        "page": page,
        "size": size,
        "total_pages": (total_count + size - 1) // size
    })


@tools_bp.post("/versions")
@openapi.definition(
    summary="获取具体工具的所有版本列表",
    description="获取具体工具的所有版本列表（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "工具名称"
                    }
                }
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
                                        "utime": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def all_versions(request):
    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    tools = await Tool.filter(name=name, deleted=False,
                              maintain=maintenance_mode if maintenance_mode else False).order_by("-ctime").values()

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
                    "name": {
                        "type": "string",
                        "description": "工具名称"
                    },
                    "version": {
                        "type": "string",
                        "description": "工具版本号"
                    }
                }
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
                                                            "description": {"type": "string"}
                                                        }
                                                    }
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
                                                            "status": {"type": "integer"}
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def tool_actions(request):
    name = request.json.get("name")
    version = request.json.get("version")
    if not name or not version:
        raise CustomError(ErrorCode.ParameterError)

    tool = await Tool.get_or_none(name=name, version=version, deleted=False)
    if not tool:
        raise CustomError(ErrorCode.ToolNotFoundError)

    actions = await ToolAction.filter(tool_name=name, version_number=version).values("action", "id", "playbook")
    action_list = []
    for action in actions:
        action.get("action")["id"] = action.get("id")
        pv_list = []
        if action.get("playbook"):
            for pv_id in action.get("playbook"):
                pv = await PlaybookVersion.get_or_none(id=pv_id, deleted=False).prefetch_related("playbook")
                pv_list.append({"id": pv.playbook.id, "pv_id": pv_id, "name": pv.playbook.name, "version": pv.version,
                                "status": pv.status})
        action.get("action")["playbook"] = pv_list
        action_list.append(action.get("action"))

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
                    "name": {
                        "type": "string",
                        "description": "工具名称"
                    },
                    "version": {
                        "type": "string",
                        "description": "工具版本号"
                    },
                    "status": {
                        "type": "integer",
                        "description": "工具状态（1:有 0:无）"
                    }
                }
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
                                "properties": {
                                    "message": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:u")
async def update_tool_status(request):
    name = request.json.get("name")
    version = request.json.get("version")
    status = int(request.json.get("status"))
    if not name or not version or status not in [0, 1]:
        raise CustomError(ErrorCode.ParameterError)

    tool = await Tool.get_or_none(name=name, version=version, deleted=False)
    if not tool:
        raise CustomError(ErrorCode.ToolNotFoundError)

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
                    "name": {
                        "type": "string",
                        "description": "工具名称"
                    },
                    "version": {
                        "type": "string",
                        "description": "工具版本号"
                    }
                }
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
                                "properties": {
                                    "message": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:d")
async def delete_tool(request):
    name = request.json.get("name")
    version = request.json.get("version")
    if not name or not version:
        raise CustomError(ErrorCode.ParameterError)

    async with in_transaction():
        tool = await Tool.get(name=name, version=version, deleted=False)
        if not tool:
            return response_ok({"message": "工具删除成功"})

        if tool.status == 1:
            raise CustomError(ErrorCode.ToolHasScriptError)

        if tool.maintain:
            raise CustomError(ErrorCode.InternalDeleteError)

        tool_path = os.path.join(TOOLS_DIR, name, version)
        if os.path.exists(tool_path):
            shutil.rmtree(tool_path)

        await utils.delete_from_s3("system", f"{name}/{version}.zip", "tools")

        await ToolAction.filter(tool_name=name, version_number=version, deleted=False).update(deleted=True)
        tool.deleted = True
        tool.updater = request.ctx.session['account_info'].username
        await ToolAction.filter(tool_name=name, version_number=version, deleted=False).update(deleted=True)
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
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "工具名称"
                    }
                }
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
                                "properties": {
                                    "message": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:d")
async def delete_tool_all(request):
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    if await Tool.filter(name=name, deleted=False, maintain=True).exists():
        raise CustomError(ErrorCode.InternalDeleteError)

    if await Tool.filter(name=name, deleted=False, status=1).exists():
        raise CustomError(ErrorCode.ToolHasScriptError)

    async with in_transaction():
        tool_path = os.path.join(TOOLS_DIR, name)
        if os.path.exists(tool_path):
            shutil.rmtree(tool_path)

        for tool in await Tool.filter(name=name, deleted=False).all():
            await utils.delete_from_s3("system", f"{name}/{tool.version}.zip", "tools")

        await ToolAction.filter(tool_name=name, deleted=False).update(deleted=True)
        await ToolResourcesModel.filter(tool_name=name, deleted=False).update(deleted=True)
        await ToolResources.filter(tool_name=name, deleted=False).delete()
        await ActionPermissions.filter(tool_name=name, deleted=False).update(deleted=True)
        await Tool.filter(name=name, deleted=False).update(deleted=True)

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
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "工具name"
                    }
                }
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
                                    "resources": {
                                        "type": "object"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def tool_resource_model(request):
    tool_name = request.json.get("name")
    if not tool_name:
        raise CustomError(ErrorCode.ParameterError)

    resources = await ToolResourcesModel.filter(tool_name=tool_name, deleted=False).first().values()
    if not resources:
        raise CustomError(ErrorCode.ResourcesModelNotFoundError)

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
                    "tool_name": {
                        "type": "string",
                        "description": "工具名称"
                    },
                    "name": {
                        "type": "string",
                        "description": "资源名称"
                    },
                    "engine": {
                        "type": "string",
                        "description": "执行引擎"
                    },
                    "resources": {
                        "type": "object",
                        "description": "资源模板"
                    }
                }
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
                                    "id": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:c")
async def add_tool_resource(request):
    tool_name = request.json.get("tool_name")
    name = request.json.get("name")
    engine = request.json.get("engine")
    resources = request.json.get("resources")
    if not tool_name or resources is None or not name:
        raise CustomError(ErrorCode.ParameterError)

    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    existing_resource = await ToolResources.get_or_none(name=name, deleted=False, tool_name=tool_name,
                                                        internal=maintenance_mode if maintenance_mode else False)
    if existing_resource:
        raise CustomError(ErrorCode.ResourcesNameExistsError)

    tr = await ToolResources.create(tool_name=tool_name, name=name, engine=engine,
                                    resources=orjson.dumps(resources).decode(),
                                    internal=maintenance_mode if maintenance_mode else False,
                                    creator=request.ctx.session['account_info'].username,
                                    updater=request.ctx.session['account_info'].username)

    r = {k: resources[k].get('value') or resources[k].get('default_value', '') for k in resources}
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
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "工具名称"
                    }
                }
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
                                        "resources": {
                                            "type": "object"
                                        },
                                        "creator": {"type": "string"},
                                        "updater": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def tool_resources_detailed(request):
    tool_name = request.json.get("name")
    if not tool_name:
        raise CustomError(ErrorCode.ParameterError)

    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    resources = await ToolResources.filter(tool_name=tool_name, deleted=False,
                                           internal=maintenance_mode if maintenance_mode else False).all()
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
                    "tool_name": {
                        "type": "string",
                        "description": "工具名称"
                    },
                    "name": {
                        "type": "string",
                        "description": "资源名称"
                    },
                    "engine": {
                        "type": "string",
                        "description": "资源执行引擎"
                    },
                    "resources": {
                        "type": "object",
                        "description": "资源模板"
                    }
                }
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
                                "properties": {
                                    "message": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:u")
async def update_tool_resource(request):
    tool_name = request.json.get("tool_name")
    name = request.json.get("name")
    engine = request.json.get("engine")
    resources = request.json.get("resources") or {}
    if not tool_name or not name:
        raise CustomError(ErrorCode.ParameterError)

    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    tool_resource = await ToolResources.get_or_none(tool_name=tool_name, name=name, deleted=False,
                                                    internal=maintenance_mode if maintenance_mode else False)

    if not tool_resource:
        raise CustomError(ErrorCode.ResourcesNotFoundError)

    if engine:
        tool_resource.engine = engine

    if resources:
        resources_old = orjson.loads(tool_resource.resources)
        for key, resource in resources.items():
            if resource.get("data_type") == "password" and resource.get("value") == "******":
                resource["value"] = resources_old[key]["value"]
        tool_resource.resources = orjson.dumps(resources).decode()
        r = {k: resources[k].get('value') or resources[k].get('default_value', '') for k in resources}
        r[tool_resource.id + "_name"] = name
        await caches.get("redis_chat").set("resource_" + tool_resource.id, r)

    tool_resource.updater = request.ctx.session['account_info'].username
    await tool_resource.save()
    return response_ok({"message": "工具资源更新成功"})


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
                    "tool_name": {
                        "type": "string",
                        "description": "工具名称"
                    },
                    "name": {
                        "type": "string",
                        "description": "资源名称"
                    }
                }
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
                                "properties": {
                                    "message": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:d")
async def delete_tool_resource(request):
    tool_name = request.json.get("tool_name")
    name = request.json.get("name")
    if not tool_name or not name:
        raise CustomError(ErrorCode.ParameterError)

    maintenance_mode = await request.app.ctx.cache_client.get(request.ctx.session["id"])
    tool_resource = await ToolResources.get_or_none(tool_name=tool_name, name=name, deleted=False,
                                                    internal=maintenance_mode if maintenance_mode else False)

    if not tool_resource:
        raise CustomError(ErrorCode.ResourcesNotFoundError)

    playbooks = await ToolAction.filter(tool_name=tool_name, deleted=False).all().values("playbook")

    playbook_ids = []
    for playbook in playbooks:
        playbook_ids += playbook.get("playbook") if playbook.get("playbook") else []

    playbooks = await PlaybookVersion.filter(id__in=playbook_ids, deleted=False).all().prefetch_related("playbook")

    for playbook in playbooks:
        if playbook.flow_json:
            if tool_resource.id in str(playbook.flow_json):
                return response_ok({"message": f"当前资源已被剧本{playbook.playbook.name}使用，请仔细检查后再删除！"})

    timeds = await Timed.filter(deleted=False).all()

    for timed in timeds:
        if timed.type == 0:
            if timed.playbook_input and timed.playbook_input["flow_id"] in playbook_ids:
                return response_ok({"message": f"当前资源已被定时任务{timed.name}使用，请仔细检查后再删除！"})
        else:
            if timed.action_input and timed.action_input["resource"] == tool_resource.id:
                return response_ok({"message": f"当前资源已被定时任务{timed.name}使用，请仔细检查后再删除！"})

    await tool_resource.delete()

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
                "properties": {
                    "tool_id": {
                        "type": "string",
                        "description": "工具ID"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "下载成功"
        }
    }
)
@requires_permission("tool:r")
async def download_zip(request):
    tool_id = request.json.get("tool_id")
    if not tool_id:
        raise CustomError(ErrorCode.ParameterError)
    tool = await Tool.get_or_none(id=tool_id, deleted=False)
    if not tool:
        raise CustomError(ErrorCode.ToolNotFoundError)

    if tool.maintain:
        raise CustomError(ErrorCode.InternalToolError)

    if tool.internal:
        raise CustomError(ErrorCode.NotDownloadError)

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
                    "name": {
                        "type": "string",
                        "description": "工具名称"
                    },
                    "resource_id": {
                        "type": "string",
                        "description": "资源id"
                    },
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "动作名称，默认all"
                    },
                    "users_id": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "用户ID列表"
                    },
                    "roles_id": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "角色ID列表"
                    }
                }
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
                                    "message": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:u")
async def add_permission(request):
    name = request.json.get("name")
    resource_id = request.json.get("resource_id")
    actions = request.json.get("actions", ["all"])
    users_id = list(set(request.json.get("users_id", [])))
    roles_id = list(set(request.json.get("roles_id", [])))
    if not name:
        raise CustomError(ErrorCode.ParameterError)
    if not isinstance(users_id, list) or not isinstance(roles_id, list) or not isinstance(actions, list):
        raise CustomError(ErrorCode.ParameterError)

    if "all" in actions:
        if not await ActionPermissions.filter(tool_name=name, action_name="all", deleted=False).exists():
            await ActionPermissions.create(tool_name=name, action_name="all",
                                           action_description="动作全部动作权限", roles=[],
                                           users=[])
        permissions = await ActionPermissions.filter(tool_name=name, action_name="all", deleted=False).all()
    else:
        permissions = await ActionPermissions.filter(tool_name=name, action_name__in=actions, deleted=False).all()

    if not permissions:
        raise CustomError(ErrorCode.ActionNotFoundError)

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
                    "name": {
                        "type": "string",
                        "description": "工具名称"
                    },
                    "resource_id": {
                        "type": "string",
                        "description": "资源id"
                    },
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "动作名称，默认all"
                    }
                }
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
                                            "items": {
                                                "type": "string"
                                            }
                                        },
                                        "roles_id": {
                                            "type": "array",
                                            "items": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def select_permission_all(request):
    name = request.json.get("name")
    resource_id = request.json.get("resource_id")
    actions = request.json.get("actions", ["all"])
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    if "all" in actions:
        if not await ActionPermissions.filter(tool_name=name, action_name="all", deleted=False).exists():
            await ActionPermissions.create(tool_name=name, action_name="all",
                                           action_description="动作全部动作权限", roles=[],
                                           users=[])
        permissions = await ActionPermissions.filter(tool_name=name, action_name="all", deleted=False).all()
    else:
        permissions = await ActionPermissions.filter(tool_name=name, action_name__in=actions, deleted=False).all()

    if not permissions:
        raise CustomError(ErrorCode.ActionNotFoundError)

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

        permission_list.append({
            "name": permission.action_name,
            "description": permission.action_description,
            "users_id": user_ids,
            "roles_id": role_ids
        })

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
                                    "description": "工具名称"
                                },
                                "resource_id": {
                                    "type": "string",
                                    "description": "资源ID"
                                },
                                "actions": {
                                    "type": "array",
                                    "items": {
                                        "type": "string"
                                    },
                                    "description": "动作列表"
                                }
                            }
                        },
                        "description": "工具-资源-动作列表"
                    }
                }
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
                                            "additionalProperties": {
                                                "type": "boolean"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def judge_permission(request):
    action_groups = request.json.get("actions", [])
    if not action_groups or not isinstance(action_groups, list):
        raise CustomError(ErrorCode.ParameterError)

    user = request.ctx.session.get('account_info')
    if not user:
        raise CustomError(ErrorCode.AccountNotFound)

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

    permissions = await ActionPermissions.filter(
        reduce(operator.or_, query_conditions),
        deleted=False
    )

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
                        role_allowed = not role_ids.isdisjoint(
                            map(str, res_dict[resource_id])
                        )
                        if role_allowed:
                            break

            action_results[action] = user_allowed or role_allowed

        result.append({
            "tool_name": tool_name,
            "resource_id": resource_id,
            "permissions": action_results
        })

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
                    "name": {
                        "type": "string",
                        "description": "工具名称"
                    },
                    "tags": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "标签名称"
                    }
                }
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
                                    "message": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:u")
async def add_tag(request):
    name = request.json.get("name")
    tags = request.json.get("tags", [])
    if not name or not tags:
        raise CustomError(ErrorCode.ParameterError)

    tools = await Tool.filter(name=name, deleted=False).all()

    if not tools:
        raise CustomError(ErrorCode.ToolNotFoundError)

    for tool in tools:
        tool.tag = list(set(tool.tag or []) | set(tags))
        tool.updater = request.ctx.session['account_info'].username
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
                    "name": {
                        "type": "string",
                        "description": "工具名称"
                    },
                    "tags": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "标签名称"
                    }
                }
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
                                "properties": {
                                    "message": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:u")
async def delete_tag(request):
    name = request.json.get("name")
    tags = request.json.get("tags", [])
    if not name or not tags:
        raise CustomError(ErrorCode.ParameterError)

    tools = await Tool.filter(name=name, deleted=False).all()

    if not tools:
        raise CustomError(ErrorCode.ToolNotFoundError)

    for tool in tools:
        tool.tag = list(set(tool.tag or []) - set(tags))
        tool.updater = request.ctx.session['account_info'].username
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
                    "switch": {
                        "type": "string",
                        "description": "开关 on/off"
                    },
                    "cron": {
                        "type": "string",
                        "description": "定时任务 格式：分 时 日 月 周，如：0 48 14 ? * 1 表示每周一的14点48分执行一次(多个小时段表示用逗号分隔，周也是如此)"
                    }
                }
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
                                "properties": {
                                    "message": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:c")
async def health_check_config(request):
    switch = request.json.get("switch")
    cron = request.json.get("cron")

    # 参数验证
    if switch == 'on':
        if not cron:
            raise CustomError(ErrorCode.ParameterError)
        await Config.update_or_create(key="health_check_cron", defaults={'value': cron}, module="tool")

    await Config.update_or_create(key="health_check_switch", defaults={'value': switch}, module="tool")

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
                                    "cron": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def health_check_get(request):
    switch = await Config.get_or_none(key="health_check_switch")
    cron = await Config.get_or_none(key="health_check_cron")
    return response_ok({"switch": switch.value if switch else "off", "cron": cron.value if cron else ""})


@tools_bp.post("/action/input")
@openapi.definition(
    summary="获取工具动作输入参数",
    description="获取工具动作输入参数（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "动作ID"
                    }
                }
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
                                        "description": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def tool_action_input(request):
    action_id = request.json.get("id")
    if not action_id:
        raise CustomError(ErrorCode.ParameterError)
    action = await ToolAction.get_or_none(id=action_id, deleted=False)
    if not action:
        raise CustomError(ErrorCode.ActionNotFoundError)

    input_data = action.action["input"]

    return response_ok(input_data)


async def tool_action_update(data):
    if not isinstance(data, dict):
        raise CustomError(ErrorCode.ParameterError)

    affected_tools = set()

    for playbook_version_id, action_ids in data.items():
        for action_id in action_ids:
            action = await ToolAction.get_or_none(id=action_id, deleted=False)
            if not action:
                continue

            if not isinstance(action.playbook, list):
                action.playbook = []

            if playbook_version_id not in action.playbook:
                action.playbook.append(playbook_version_id)
                await action.save()

                affected_tools.add((action.tool_name, action.version_number))

    for tool_name, version in affected_tools:
        tool = await Tool.get_or_none(name=tool_name, version=version, deleted=False)
        if tool and tool.status != 1:
            tool.status = 1
            await tool.save()


async def tool_action_playbook_delete(data):
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
        raise CustomError(ErrorCode.ParameterError)

    affected_tools = set()

    for playbook_id, action_ids in data.items():
        for action_id in action_ids:
            action = await ToolAction.get_or_none(id=action_id, deleted=False)
            if not action:
                continue
            if not isinstance(action.playbook, list):
                action.playbook = []

            if playbook_id in action.playbook:
                action.playbook.remove(playbook_id)
                await action.save()
            affected_tools.add((action.tool_name, action.version_number))

    for tool_name, version in affected_tools:
        actions = await ToolAction.filter(tool_name=tool_name, version_number=version, deleted=False).all()
        has_playbook = any(action.playbook for action in actions)
        tool = await Tool.get_or_none(name=tool_name, version=version, deleted=False)
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
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "标签名称"
                    }
                }
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
                            "data": {
                                "type": "string"
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:c")
async def new_tag(request):
    name = request.json.get("name", "").strip()
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    if await Tag.get_or_none(name=name, type=1):
        raise CustomError(ErrorCode.TagExistError)

    await Tag.create(name=name, type=1)

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
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def tag_list(request):
    tags = await Tag.filter(type=1, deleted=False).all()

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
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "标签名称"
                    }
                }
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
                                "type": "string"
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:d")
async def remove_tag(request):
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    tag = await Tag.get(name=name, type=1)
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
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:r")
async def engine_list(request):
    c = caches.get("redis_action").client
    lion_keys = []
    async for key in c.scan_iter(match="lion_engine_*", count=100):
        lion_keys.append(key.decode().replace("lion_engine_", ""))

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
                                "description": "类型 flow/action"
                            },
                            "app_name": {
                                "type": "string",
                                "description": "应用名称，英文标识"
                            },
                            "name": {
                                "type": "string",
                                "description": "应用中文名称"
                            },
                            "app_ver": {
                                "type": "string",
                                "description": "应用版本号"
                            },
                            "main": {
                                "type": "string",
                                "description": "主程序文件名"
                            },
                            "action_input": {
                                "type": "object",
                                "description": "动作输入参数"
                            },
                            "node_name": {
                                "type": "string",
                                "description": "节点名称"
                            },
                            "action": {
                                "type": "string",
                                "description": "动作名称 英文标识"
                            },
                            "action_name": {
                                "type": "string",
                                "description": "动作名称 中文名称"
                            },
                            "retry_times": {
                                "type": "integer",
                                "description": "重试次数"
                            },
                            "timeout": {
                                "type": "integer",
                                "description": "超时时间,单位秒"
                            },
                            "resource": {
                                "type": "string",
                                "description": "进行资源检查的id"
                            }
                        }
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "健康检查执行成功"
        }
    }
)
@requires_permission("tool:r")
async def health_check(request):
    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
    utime = datetime.datetime.now(TZ)
    message = request.json.get("action_input", {})
    if message.get("type") in ('flow', 'action'):
        run_id = gen_run_id()
        message["executor"] = request.ctx.session['account_info'].id
        msg = {"run_type": message['type'], "run_id": run_id, "run_args": message}
        await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)
        sent_id = []
        for i in range(60):
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
                "properties": {
                    "data": {
                        "type": "string",
                        "description": "python脚本代码"
                    }
                }
            }
        }
    },
    response={
        "200": {
            "description": "调试执行成功"
        }
    }
)
@requires_permission("tool:r")
async def tool_debug(request):
    resp = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
    data = request.json.get("data")
    if not data:
        raise CustomError(ErrorCode.ParameterError)

    tool = await Tool.filter(name="lion_mgmt", deleted=False).order_by("-ctime").first()
    if not tool:
        return response_ok("工具开发运行APP缺失，请联系管理员处理")

    resource = await ToolResources.filter(tool_name="lion_mgmt", deleted=False).order_by("-ctime").first()
    if not resource:
        raise response_ok("工具开发运行资源不存在，请联系管理员处理")

    user = request.ctx.session['account_info']
    params = {"resource": resource.id, "node_name": tool.description,
              "action": "run_python_script", "action_name": "执行python脚本", "app_name": "lion_mgmt",
              "name": tool.description, "app_ver": tool.version, "main": tool.main, "action_input": {"data": data},
              "retry_times": 0, "timeout": 300,
              "executor": user.id}
    run_id = gen_run_id()
    msg = {"run_type": "action", "run_id": run_id, "run_args": params}
    await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)
    sent_id = []
    for i in range(60):
        logs = await Runlog.filter(run_id=run_id).all()
        if logs:
            for log in logs:
                if log.id not in sent_id:
                    sent_id.append(log.id)
                    result = orjson.loads(log.result)
                    data = result.get("data")
                    if data:
                        await resp.send(f"data: {data}\n\n")
            if any(log.status in [-1, 2] for log in logs):
                break
            break
        await asyncio.sleep(1)

    if resp:
        await resp.eof()

    return resp


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
                    "key": {
                        "type": "string",
                        "description": "键"
                    },
                    "value": {
                        "type": "string",
                        "description": "值"
                    },
                    "module": {
                        "type": "string",
                        "description": "所属模块"
                    }
                }
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
                            "data": {
                                "type": "string"
                            }
                        }
                    }
                }
            }
        }
    }
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
                            "data": {
                                "type": "object"
                            }
                        }
                    }
                }
            }
        }
    }
)
@requires_permission("tool:u")
async def get_health_check_result(request):
    result = await Config.get_or_none(key="health_check", module="tools")
    return response_ok(result.json if result else {})
