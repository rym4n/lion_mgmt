# coding=utf-8
import asyncio
import base64
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

import aiofile
import orjson
from sanic import Blueprint, response
from tortoise.expressions import Q
from tortoise.functions import Max
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs import utils
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission, load_license
from models import Tool, ToolAction, ToolResources, ToolResourcesModel, ActionPermissions, Config, Tag
from settings import TOOLS_DIR

# 定义 Blueprint
tools_bp = Blueprint("tools", version=1, url_prefix="/tools")
__all__ = ["tools_bp"]

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


def fix_zip_filename(filename: str) -> str:
    try:
        return filename.encode('cp437').decode('gbk')
    except UnicodeDecodeError:
        try:
            return filename.encode('cp437').decode('utf-8')
        except:
            return filename


@tools_bp.post("/upload")
@requires_permission("tool:c")
async def upload_and_extract_zip(request):
    """
    上传工具接口（需要工具创建权限）
    请求参数:
        - file: ZIP 文件 (multipart/form-data)
    """
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

        for key, item in lic["grouplist"].items():
            if key == "应用集成":
                if int(item["expireTime"]) > int(time.time() * 1000):
                    total_up_value = int(item["value"])

        if total_up_value != 99999 and total_count >= total_up_value:
            return response_ok({"error": "应用数量已达到上限"})

        header = zip_file.body[:4]
        if header[:2] != b'PK' or header[2:] != b'\x03\x04' or not zip_file.name.lower().endswith('.zip'):
            results.append({"tool": zip_file.name, "msg": "此文件不是标准ZIP文件，请重新上传"})
            data += 1
            percent = int((data * single_progress))
            success_progress = percent
            await resp.send(f"data: 解压进度：{percent}%\n\n")
            continue

        config_data, version, tool_name, msg = await unzip(zip_file.body, creator, single_progress, resp,
                                                           success_progress, request.ctx.cache_client)
        if msg != "success":
            results.append({"tool": tool_name, "msg": msg})
            data += 1
            percent = int((data * single_progress))
            success_progress = percent
            await resp.send(f"data: 解压进度：{percent}%\n\n")
            continue

        if "actions" in config_data.keys():
            action_key = "actions"
        else:
            results.append({"tool": tool_name, "msg": "config文件内容错误，缺失actions字段"})
            data += 1
            percent = int((data * single_progress))
            success_progress = percent
            continue
        for action in config_data.get(action_key):
            await ToolAction.create(tool_name=config_data.get("name"), version_number=version,
                                    action=action)
            actions = await ActionPermissions.filter(tool_name=tool_name, deleted=False).values("action_name")
            action_names = {action["action_name"] for action in actions}

            if action["func"] not in action_names:
                await ActionPermissions.create(tool_name=tool_name, action_name=action["func"],
                                               action_description=action.get("name") or "", roles_id=[],
                                               users_id=[])
        configuration = await ToolResourcesModel.filter(tool_name=tool_name, deleted=False).first().values("resources")
        if not configuration or configuration["resources"] != config_data.get("resource"):
            await ToolResourcesModel.create(tool_name=tool_name, resources=config_data.get("resource") or {})

        results.append({"tool": tool_name, "msg": msg})

        data += 1
        percent = int((data * single_progress))
        success_progress = percent
        await resp.send(f"data: 解压进度：{percent}%\n\n")

    if resp:
        await resp.send(f"data: {results}\n\n")
        await resp.eof()

    return resp


async def unzip(zip_data, creator, single_progress, resp, success_progress, redis_client):
    """
    解压 ZIP 文件，直接从文件流读取，并进行文件结构校验
    """
    data = 0
    zip_buffer = BytesIO(zip_data)

    with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
        version = None

        files_info = zip_ref.infolist()

        actual_files = {os.path.basename(fix_zip_filename(f.filename)) for f in files_info if not f.is_dir()}
        py_files_dict = {os.path.basename(f.filename): f for f in files_info if f.filename.endswith(".py")}
        config_path = next((f for f in files_info if os.path.basename(f.filename) == "config.json"), None)

        single_progress = single_progress / 10

        with zip_ref.open(config_path) as config_file:
            config_data = orjson.loads(config_file.read().decode("utf-8"))

        tool_name = config_data.get("name")
        pattern = r'^[A-Za-z][A-Za-z0-9_]*$'
        if not bool(re.match(pattern, tool_name)):
            return config_data, version, tool_name, "工具名称不合法"

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
            logo_name = logo_name.split("/")[1]

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

        if not logo_found:
            return config_data, version, tool_name, "缺少logo文件"

        data += 1
        percent = int(data * single_progress) + success_progress
        await resp.send(f"data: 解压进度：{percent}%\n\n")

        with zip_ref.open(py_files_dict[py_file_name]) as py_file:
            py_data = py_file.read().decode("utf-8")

        if any(f"def {class_name}(" not in py_data for class_name in class_name_list):
            return config_data, version, tool_name, "Python文件内容错误"
        data += 1
        percent = int(data * single_progress) + success_progress
        await resp.send(f"data: 解压进度：{percent}%\n\n")

        configuration = await ToolResourcesModel.filter(tool_name=name, deleted=False).first().values("resources")
        if configuration and configuration["resources"] != config_data.get("resource", {}):
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

        extra_path = os.path.join(TOOLS_DIR, name, version)
        os.makedirs(extra_path, exist_ok=True)

        await utils.unzip(zip_data, extra_path)

        config_parent_dir = os.path.dirname(config_path.filename)
        save_path = os.path.join(extra_path, config_parent_dir).replace("\\", "/")

        tool = await Tool.create(
            name=name, version=version, path=save_path,
            description=config_data.get("description"),
            creator=creator, updater=creator,
            hash_value=hasher.hexdigest(),
            internal=config_data.get("internal", False),
            main=py_file_name,
        )

        # 将config数据存入redis
        await redis_client.set(name + "_" + version, config_data, ttl=2592000)

        return config_data, version, tool.name, "success"


@tools_bp.post("/all")
@requires_permission("tool:r")
async def all_tools(request):
    """
    获取所有工具列表（仅显示每个工具的最新版本，并对工具名称进行模糊搜索）

    query参数:
        - page: 页码（默认1）     int
        - size: 每页数量（默认10）     int
        - key: 名称和描述的搜索（可选，模糊搜索）   str
        - tag: 标签名称（可选，精确搜索）   str
    """
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 10))
    key = request.json.get("key", "").strip()

    subquery = Tool.filter(deleted=False).annotate(max_version=Max("version")).group_by("name")
    if key:
        subquery = subquery.filter(Q(name__icontains=key) | Q(description__icontains=key))
    if request.json.get("tag"):
        subquery = subquery.filter(tag__contains=[request.json.get("tag")])
    subquery_results = await subquery.values("name", "max_version")
    if not subquery_results:
        return response_ok({
            "events": [],
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

    latest_tools = await asyncio.gather(*[
        enrich_tool(tool, request.ctx.cache_client) for tool in latest_tools
    ])

    total_count = await Tool.filter(condition).count()

    return response_ok({
        "events": latest_tools,
        "total": total_count,
        "page": page,
        "size": size,
        "total_pages": (total_count + size - 1) // size
    })


async def enrich_tool(tool, cache_client):
    cache_key = f"{tool['name']}_{tool['version']}"
    data = await cache_client.get(cache_key)

    if not data:
        config_path = os.path.join(tool["path"], "config.json")
        async with aiofile.AIOFile(config_path, 'r', encoding='utf-8') as f:
            data = await f.read()
            await cache_client.set(cache_key, data, ttl=2592000)
        data = orjson.loads(data)
    else:
        if isinstance(data, (str, bytes)):
            data = orjson.loads(data)

    logo_path = os.path.join(tool["path"], data.get("logo"))
    if os.path.exists(logo_path):
        async with aiofile.AIOFile(logo_path, 'rb') as logo_file:
            logo_data = await logo_file.read()
            tool["logo"] = base64.b64encode(logo_data).decode("utf-8")
    tool.pop("path", None)
    return tool


@tools_bp.post("/versions")
@requires_permission("tool:r")
async def all_versions(request):
    """
    获取具体工具的所有版本列表（需要工具查看权限）

    query参数:
        - name: 工具名称   str
    """
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    tools = await Tool.filter(name=name, deleted=False).order_by("-ctime").values()

    for tool in tools:
        tool.pop("path")

    return response_ok(tools)


@tools_bp.post("/actions")
@requires_permission("tool:r")
async def tool_actions(request):
    """
    获取工具当前版本的所有动作列表（需要工具查看权限）

    query参数:
        - name: 工具名称    str
        - version: 工具版本号    str
    """
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
        action.get("action")["playbook"] = action.get("playbook")
        action_list.append(action.get("action"))

    return response_ok({"actions": action_list})


@tools_bp.post("/upstatus")
@requires_permission("tool:u")
async def update_tool_status(request):
    """
    更新工具状态（需要工具更新权限）

    query参数:
        - name: 工具名称    str
        - version: 工具版本号    str
        - status: 工具状态（1:有 0:无） int
    """
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
@requires_permission("tool:d")
async def delete_tool(request):
    """
    删除指定版本工具（需要工具删除权限）

    query参数:
        - name: 工具名称
        - version: 工具版本号
    """
    name = request.json.get("name")
    version = request.json.get("version")
    if not name or not version:
        raise CustomError(ErrorCode.ParameterError)

    async with in_transaction():
        tool = await Tool.get(name=name, version=version, deleted=False)
        if not tool:
            raise CustomError(ErrorCode.ToolAlreadyDeletedError)

        if tool.status == 1:
            raise CustomError(ErrorCode.ToolHasScriptError)

        tool_path = os.path.join(TOOLS_DIR, name, version)
        if os.path.exists(tool_path):
            shutil.rmtree(tool_path)

        tool.deleted = True
        tool.updater = request.ctx.session['account_info'].username
        await tool.save()

    return response_ok({"message": "工具删除成功"})


@tools_bp.post("/delete/all")
@requires_permission("tool:d")
async def delete_tool_all(request):
    """
    删除工具全部版本（需要工具删除权限）

    query参数:
        - name: 工具名称
    """
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    if await Tool.filter(name=name, deleted=False, status=1).exists():
        raise CustomError(ErrorCode.ToolHasScriptError)

    async with in_transaction():
        tool_path = os.path.join(TOOLS_DIR, name)
        if os.path.exists(tool_path):
            shutil.rmtree(tool_path)

        await ToolAction.filter(tool_name=name, deleted=False).update(deleted=True)
        await ToolResourcesModel.filter(tool_name=name, deleted=False).update(deleted=True)
        await ToolResources.filter(tool_name=name, deleted=False).update(deleted=True)
        await ActionPermissions.filter(tool_name=name, deleted=False).update(deleted=True)
        await Tool.filter(name=name, deleted=False).update(deleted=True)

    return response_ok({"message": "全部工具删除成功"})


@tools_bp.post("/resource/model")
@requires_permission("tool:r")
async def tool_resource_model(request):
    """
    获取工具的资源模型（需要工具查看权限）

    query参数:
        - name: 工具name    str
    """
    tool_name = request.json.get("name")
    if not tool_name:
        raise CustomError(ErrorCode.ParameterError)

    resources = await ToolResourcesModel.filter(tool_name=tool_name, deleted=False).first().values()
    if not resources:
        raise CustomError(ErrorCode.ResourcesModelNotFoundError)

    return response_ok(resources)


@tools_bp.post("/resource/add")
@requires_permission("tool:c")
async def add_tool_resource(request):
    """
    新增工具资源（需要工具创建权限）

    query参数:
        - tool_name: 工具名称    str
        - name: 资源名称    str
        - engine: 执行引擎    str
        - resources: 资源模板   dict
                {"Lizhu_username": {
                        "order": 0,
                        "options": "",
                        "required": true,
                        "data_type": "string",
                        "description": "离朱安全验证平台登录用户名",
                        "default_value": "admin",
                        "value":"test"      这个是新增的值
                }}
    """
    tool_name = request.json.get("tool_name")
    name = request.json.get("name")
    engine = request.json.get("engine")
    resources = request.json.get("resources")
    if tool_name is None or resources is None or name is None:
        raise CustomError(ErrorCode.ParameterError)

    existing_resource = await ToolResources.get_or_none(name=name, deleted=False)
    if existing_resource:
        raise CustomError(ErrorCode.ResourcesNameExistsError)

    resources = await ToolResources.create(tool_name=tool_name, name=name, engine=engine,
                                           resources=resources,
                                           creator=request.ctx.session['account_info'].username
                                           , updater=request.ctx.session['account_info'].username)

    return response_ok({"message": "工具资源新增成功", "id": resources.id})


@tools_bp.post("/resource/detailed")
@requires_permission("tool:r")
async def tool_resources_detailed(request):
    """
    获取工具的资源详情（需要工具查看权限）

    query参数:
        - name: 工具名称    str
    """
    tool_name = request.json.get("name")
    if not tool_name:
        raise CustomError(ErrorCode.ParameterError)

    resources = await ToolResources.filter(tool_name=tool_name, deleted=False).all().values()
    if not resources:
        return response_ok("工具资源不存在")

    for resource in resources:
        for value in resource["resources"].values():
            if value["data_type"] == "password":
                value["value"] = "******"

    return response_ok(resources)


@tools_bp.post("/resource/update")
@requires_permission("tool:u")
async def update_tool_resource(request):
    """
    更新工具资源（需要工具更新权限）

    query参数:
        - tool_name: 工具名称    str\
        - name: 资源名称    str  不可修改，修改其他值的凭证
        - engine: 资源描述    str    可选
        - resources: 资源模板   dict    可选
                {"Lizhu_username": {
                        "order": 0,
                        "options": "",
                        "required": true,
                        "data_type": "string",
                        "description": "离朱安全验证平台登录用户名",
                        "default_value": "admin",
                        "value":"test"      这个是新增的值
                }}
    """
    tool_name = request.json.get("tool_name")
    name = request.json.get("name")
    engine = request.json.get("engine", None)
    resources = request.json.get("resources", None)
    if not tool_name or not name:
        raise CustomError(ErrorCode.ParameterError)

    tool_resource = await ToolResources.get_or_none(tool_name=tool_name, name=name, deleted=False)

    if not tool_resource:
        raise CustomError(ErrorCode.ResourcesNotFoundError)

    if engine:
        tool_resource.engine = engine
    if resources:
        tool_resource.resources = resources

    tool_resource.updater = request.ctx.session['account_info'].username
    await tool_resource.save()
    return response_ok({"message": "工具资源更新成功"})


@tools_bp.post("/resource/delete")
@requires_permission("tool:d")
async def delete_tool_resource(request):
    """
    删除工具资源（需要工具删除权限）

    query参数:
        - tool_name: 工具名称    str
        - name: 资源名称    str
    """
    tool_name = request.json.get("tool_name")
    name = request.json.get("name")
    if not tool_name or not name:
        raise CustomError(ErrorCode.ParameterError)

    tool_resource = await ToolResources.get_or_none(tool_name=tool_name, name=name, deleted=False)

    if not tool_resource:
        raise CustomError(ErrorCode.ResourcesNotFoundError)

    tool_resource.deleted = True
    tool_resource.updater = request.ctx.session['account_info'].username
    await tool_resource.save()

    return response_ok({"message": "工具资源删除成功"})


@tools_bp.post("/download")
@requires_permission("tool:r")
async def download_zip(request):
    """
    下载工具 ZIP 文件接口   （需要工具查看权限）

    请求参数:
        - tool_id: 工具ID
    """
    tool_id = request.json.get("tool_id")
    if not tool_id:
        raise CustomError(ErrorCode.ParameterError)
    tool = await Tool.get_or_none(id=tool_id, deleted=False)
    if not tool:
        raise CustomError(ErrorCode.ToolNotFoundError)

    if tool.internal:
        raise CustomError(ErrorCode.InternalToolError)

    zip_path = os.path.abspath(os.path.join(os.path.dirname(tool.path), f"SourceFile.zip"))

    tool_name = tool.name
    version = tool.version

    if not os.path.exists(zip_path):
        raise CustomError(ErrorCode.ToolNotFoundError)

    return await response.file(zip_path, filename=f"{tool_name}_{version}.zip", mime_type="application/zip")


@tools_bp.post("/action/permissions/add")
@requires_permission("tool:u")
async def add_permission(request):
    """
    新增工具动作权限（需要工具更新权限）

    query参数:
        - name: 工具名称    str
        - resource_id: 资源id    str
        - actions: 动作名称    list    可选，默认all
        - users_id: 用户ID列表    list
        - roles_id: 角色ID列表    list
    """
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
        permissions = await ActionPermissions.filter(tool_name=name, deleted=False).all()
    else:
        permissions = await ActionPermissions.filter(tool_name=name, action_name__in=actions, deleted=False).all()

    if not permissions:
        raise CustomError(ErrorCode.ActionNotFoundError)

    for permission in permissions:
        user_dict_list = []
        if isinstance(permission.users_id, list):
            user_dict_list = permission.users_id.copy()
        elif isinstance(permission.users_id, str):
            try:
                user_dict_list = orjson.loads(permission.users_id)
            except orjson.JSONDecodeError:
                user_dict_list = []

        if not user_dict_list or not isinstance(user_dict_list[0], dict):
            user_dict_list = [{}]

        current_users = user_dict_list[0].get(resource_id, [])
        merged_users = list(set(current_users) | set(users_id))
        user_dict_list[0][resource_id] = merged_users

        role_dict_list = []
        if isinstance(permission.roles_id, list):
            role_dict_list = permission.roles_id.copy()
        elif isinstance(permission.roles_id, str):
            try:
                role_dict_list = orjson.loads(permission.roles_id)
            except orjson.JSONDecodeError:
                role_dict_list = []

        if not role_dict_list or not isinstance(role_dict_list[0], dict):
            role_dict_list = [{}]

        current_roles = role_dict_list[0].get(resource_id, [])
        merged_roles = list(set(current_roles) | set(roles_id))
        role_dict_list[0][resource_id] = merged_roles

        permission.users_id = user_dict_list
        permission.roles_id = role_dict_list

    await ActionPermissions.bulk_update(permissions, fields=["users_id", "roles_id"])

    return response_ok({"message": "工具动作权限新增成功"})


@tools_bp.post("/action/permissions/delete")
@requires_permission("tool:u")
async def delete_permission(request):
    """
    删除工具动作权限（需要工具更新权限）

    query参数:
        - name: 工具名称    str
        - resource_id: 资源id    str
        - actions: 动作名称    list    可选，默认all
        - users_id: 用户ID列表    list
        - roles_id: 角色ID列表    list
    """
    name = request.json.get("name")
    resource_id = request.json.get("resource_id")
    actions = request.json.get("actions", ["all"])
    users_id = list(set(request.json.get("users_id", [])))
    roles_id = list(set(request.json.get("roles_id", [])))
    if not name:
        raise CustomError(ErrorCode.ParameterError)
    if not isinstance(users_id, list) or not isinstance(roles_id, list) or not isinstance(actions, list):
        raise CustomError(ErrorCode.ParameterError)

    permissions = await ActionPermissions.filter(
        tool_name=name,
        deleted=False,
        **({"action_name__in": actions} if "all" not in actions else {})
    ).all()

    if not permissions:
        raise CustomError(ErrorCode.ActionNotFoundError)

    for permission in permissions:
        user_dict_list = []
        if isinstance(permission.users_id, list):
            user_dict_list = permission.users_id.copy()
        elif isinstance(permission.users_id, str):
            try:
                user_dict_list = orjson.loads(permission.users_id)
            except orjson.JSONDecodeError:
                user_dict_list = []

        if not user_dict_list or not isinstance(user_dict_list[0], dict):
            user_dict_list = [{}]

        if resource_id in user_dict_list[0]:
            original_users = set(user_dict_list[0][resource_id])
            removed_users = set(users_id)
            updated_users = list(original_users - removed_users)

            if updated_users:
                user_dict_list[0][resource_id] = updated_users
            else:
                del user_dict_list[0][resource_id]
                if not user_dict_list[0]:
                    user_dict_list = []

        permission.users_id = user_dict_list

        role_dict_list = []
        if isinstance(permission.roles_id, list):
            role_dict_list = permission.roles_id.copy()
        elif isinstance(permission.roles_id, str):
            try:
                role_dict_list = orjson.loads(permission.roles_id)
            except orjson.JSONDecodeError:
                role_dict_list = []

        if not role_dict_list or not isinstance(role_dict_list[0], dict):
            role_dict_list = [{}]

        if resource_id in role_dict_list[0]:
            original_roles = set(role_dict_list[0][resource_id])
            removed_roles = set(roles_id)
            updated_roles = list(original_roles - removed_roles)

            if updated_roles:
                role_dict_list[0][resource_id] = updated_roles
            else:
                del role_dict_list[0][resource_id]
                if not role_dict_list[0]:
                    role_dict_list = []

        permission.roles_id = role_dict_list

    await ActionPermissions.bulk_update(
        permissions,
        fields=["users_id", "roles_id"]
    )

    return response_ok({"message": "工具动作权限删除成功"})


@tools_bp.post("/action/permissions/select")
@requires_permission("tool:r")
async def select_permission_all(request):
    """
    获取工具全部动作权限（需要工具查看权限）

    query参数:
        - name: 工具名称    str
        - resource_id: 资源id    str
        - actions: 动作名称    list    可选，默认all
    """
    name = request.json.get("name")
    resource_id = request.json.get("resource_id")
    actions = request.json.get("actions", ["all"])
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    if "all" in actions:
        permissions = await ActionPermissions.filter(tool_name=name, deleted=False).all()
    else:
        permissions = await ActionPermissions.filter(tool_name=name, action_name__in=actions, deleted=False).all()

    if not permissions:
        raise CustomError(ErrorCode.ActionNotFoundError)

    permission_list = []
    for permission in permissions:
        users_data = []
        if isinstance(permission.users_id, list):
            users_data = permission.users_id
        elif isinstance(permission.users_id, str):
            try:
                users_data = orjson.loads(permission.users_id)
            except orjson.JSONDecodeError:
                users_data = []

        user_ids = []
        if users_data and isinstance(users_data[0], dict):
            user_ids = users_data[0].get(resource_id, [])

        roles_data = []
        if isinstance(permission.roles_id, list):
            roles_data = permission.roles_id
        elif isinstance(permission.roles_id, str):
            try:
                roles_data = orjson.loads(permission.roles_id)
            except orjson.JSONDecodeError:
                roles_data = []

        role_ids = []
        if roles_data and isinstance(roles_data[0], dict):
            role_ids = roles_data[0].get(resource_id, [])

        permission_list.append({
            "name": permission.action_name,
            "users_id": user_ids,
            "roles_id": role_ids
        })

    return response_ok(permission_list)


@tools_bp.post("/action/permissions/judge")
@requires_permission("tool:r")
async def judge_permission(request):
    """
    多资源权限判断接口 (适配列表字典结构)

    请求体参数:
        - actions: 工具-资源-动作列表
            [
                {
                    "tool_name": "工具名称",
                    "resource_id": "资源ID",
                    "actions": ["动作1", "动作2"]
                },
                ...
            ]
    """
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
            if permission.users_id:
                for res_dict in permission.users_id:
                    if resource_id in res_dict:
                        user_allowed = user_id in map(str, res_dict[resource_id])
                        if user_allowed:
                            break

            role_allowed = False
            if permission.roles_id:
                for res_dict in permission.roles_id:
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
@requires_permission("tool:u")
async def add_tag(request):
    """
    新增工具标签（需要工具更新权限）

    query参数:
        - name: 工具名称    str
        - tags: 标签名称    list
    """
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
@requires_permission("tool:u")
async def delete_tag(request):
    """
    删除工具标签（需要工具更新权限）

    query参数:
        - name: 工具名称    str
        - tags: 标签名称    list
    """
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
@requires_permission("tool:u")
async def health_check_config(request):
    """
    修改工具健康检查配置信息（需要工具更新权限）

    query参数:
        switch: 开关    str    on/off
        cron: 定时任务    str    格式：分 时 日 月 周，如：0 48 14 ? * 1 表示每周一的14点48分执行一次(多个小时段表示用逗号分隔，周也是如此)
    """
    switch = request.json.get("switch")
    cron = (request.json.get("cron") or None)

    # 参数验证
    if not switch:
        raise CustomError(ErrorCode.ParameterError)

    key = "health_check_config"
    config = await Config.get_or_none(key=key)

    if not config:
        config = await Config.create(key=key, value={"cron": cron}, module=["tool"])
        config.deleted = (switch == "off")
    else:
        config.deleted = (switch == "off")
        config.value = orjson.dumps({"cron": cron}).decode()
    await config.save()

    return response_ok({"message": "资源健康检查配置修改成功"})


@tools_bp.post("/health/check/get")
@requires_permission("tool:r")
async def health_check_get(request):
    """
    获取健康检测配置信息（需要工具查看权限）

    请求参数：
        无
    """
    config = await Config.get_or_none(key="health_check_config")
    if not config:
        return response_ok({"switch": "off", "cron": None})

    value = config.value

    return response_ok({"switch": "on" if not config.deleted else "off", "cron": value.get("cron")})


@tools_bp.post("/action/input")
@requires_permission("tool:r")
async def tool_action_input(request):
    """
    获取工具动作输入参数（需要工具查看权限）

    query参数:
        - id : 动作ID     int
    """
    action_id = request.json.get("id")
    if not action_id:
        raise CustomError(ErrorCode.ParameterError)
    action = await ToolAction.get_or_none(id=action_id, deleted=False)
    if not action:
        raise CustomError(ErrorCode.ActionNotFoundError)

    input_data = action.action["input"]

    return response_ok(input_data)


async def tool_action_update(data):
    """
    为动作绑定剧本id
        - data   动作数据    dict
            {
                "剧本id": [
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
                action.playbook_version = []

            if playbook_id not in action.playbook:
                action.playbook_version.append(playbook_id)
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
                "剧本id": [
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
                action.playbook_version = []

            if playbook_id in action.playbook:
                action.playbook_version.remove(playbook_id)
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
@requires_permission("tool:c")
async def new_tag(request):
    """
    新建标签（需要工具新增权限）

    请求参数：
        - name: 标签名称    str
    """
    name = request.json.get("name", "").strip()
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    if await Tag.get_or_none(name=name, type=1):
        raise CustomError(ErrorCode.TagExistError)

    await Tag.create(name=name, type=1)

    return response_ok("标签新建成功")


@tools_bp.post("/tag/list")
@requires_permission("tool:r")
async def tag_list(request):
    """
    获取标签列表（需要工具查看权限）

    请求参数：
        无
    """
    tags = await Tag.filter(type=1, deleted=False).all()

    return response_ok([tag.name for tag in tags])


@tools_bp.post("/tag/remove")
@requires_permission("tool:d")
async def remove_tag(request):
    """
    删除标签（需要工具删除权限）

    请求参数：
        - name: 标签名称    str
    """
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.ParameterError)

    tag = await Tag.get(name=name, type=1)
    await tag.delete()

    return response_ok("标签删除成功")
