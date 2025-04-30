# coding=utf-8
import hashlib
import os
import time
import zipfile
from io import BytesIO

import orjson
from sanic import Blueprint, response
from tortoise.expressions import Q
from tortoise.functions import Max

from core.error_code import ErrorCode
from core.response import response_ok
from libs import utils
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import del_dirs
from models import Tool, ToolAction, ToolResources, ToolResourcesModel, ActionPermissions, Config
from settings import TOOLS_DIR, LICENSE_DATA

# 定义 Blueprint
tool_management_bp = Blueprint("tool_management", url_prefix="v1/tool_management")
__all__ = ["tool_management_bp"]

# 预期的文件结构
EXPECTED_STRUCTURE = {
    "required_files": ["config.json"]
}

connected_clients = {}


@tool_management_bp.post("/upload")
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
    for zip_file in zip_files:
        total_count = await Tool.filter(deleted=False, internal=False).count()
        if LICENSE_DATA:
            for item in LICENSE_DATA["menuInfo"]["moduleList"]:
                if item["name"] == "应用数量":
                    if item["auth"] and int(item["expireTime"]) > int(time.time() * 1000):
                        total_up_value = int(item["value"])

        if total_count >= total_up_value:
            return response_ok({"error": "应用数量已达到上限"})

        config_data, version, tool_name, tool_id, msg = await unzip(zip_file.body, creator, single_progress, resp,
                                                           success_progress)
        if msg != "success":
            results.append({"tool": tool_name, "msg": msg})
            data += 1
            percent = int((data * single_progress))
            success_progress = percent
            await resp.send(f"data: 解压进度：{percent}%\n\n")
            continue

        # 将config数据存入redis
        redis_client = request.app.ctx.cache_client
        await redis_client.set(tool_id, config_data, ttl=2592000)

        if "actions" in config_data.keys():
            action_key = "actions"
        elif "action" in config_data.keys():
            action_key = "action"
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

            if action["name"] not in action_names:
                await ActionPermissions.create(tool_name=tool_name, action_name=action["name"],
                                               action_description=action.get("description") or "", roles_id=[], users_id=[])
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


async def unzip(zip_data, creator, single_progress, resp, success_progress):
    """
    解压 ZIP 文件，直接从文件流读取，并进行文件结构校验
    """
    data = 0
    zip_buffer = BytesIO(zip_data)

    with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
        version = None

        files_info = zip_ref.infolist()

        actual_files = {os.path.basename(f.filename) for f in files_info if not f.is_dir()}
        py_files_dict = {os.path.basename(f.filename): f for f in files_info if f.filename.endswith(".py")}
        config_path = next((f for f in files_info if os.path.basename(f.filename) == "config.json"), None)

        single_progress = single_progress / 10

        with zip_ref.open(config_path) as config_file:
            config_data = orjson.loads(config_file.read().decode("utf-8"))

        tool_name = config_data.get("name")

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
        elif "action" in config_data.keys():
            action_key = "action"
        else:
            return config_data, version, tool_name, "config文件内容错误，缺失actions字段"
        class_name_list = [action.get("func") for action in config_data.get(action_key, [])]

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
            return config_data, version, tool_name, "logo文件不存在"
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
        if configuration and configuration["resources"] != config_data.get("resource"):
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

        return config_data, version, tool.name, tool.id,"success"


@tool_management_bp.post("/all")
@requires_permission("tool:r")
async def all_tools(request):
    """
    获取所有工具列表（仅显示每个工具的最新版本，并对工具名称进行模糊搜索）

    query参数:
        - page: 页码（默认1）     int
        - size: 每页数量（默认10）     int
        - key: 名称和描述的搜索（可选，模糊搜索）   str
    """
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 10))
    key = request.json.get("key", "").strip()

    subquery = Tool.filter(deleted=False).annotate(max_version=Max("version")).group_by("name")
    if key:
        subquery = subquery.filter(Q(name__icontains=key) | Q(description__icontains=key))
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

    for latest_tool in latest_tools:
        latest_tool.pop("path")

    total_count = await Tool.filter(condition).count()

    return response_ok({
        "events": latest_tools,
        "total": total_count,
        "page": page,
        "size": size,
        "total_pages": (total_count + size - 1) // size
    })


@tool_management_bp.post("/versions")
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

    tool = await Tool.filter(name=name, deleted=False).order_by("-ctime").first()
    if not tool:
        raise CustomError(ErrorCode.ToolNotFoundError)

    tools = await Tool.filter(name=name, deleted=False).order_by("-ctime").values()

    for tool in tools:
        tool.pop("path")

    return response_ok(tools)


@tool_management_bp.post("/actions")
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

    tool = await Tool.filter(name=name, version=version, deleted=False).first()
    if not tool:
        raise CustomError(ErrorCode.ToolNotFoundError)

    actions = await ToolAction.filter(tool_name=name, version_number=version).values("action")
    action_list = [action.get("action") for action in actions]

    return response_ok({"actions": action_list})


@tool_management_bp.post("/upstatus")
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

    tool = await Tool.filter(name=name, version=version, deleted=False).first()
    if not tool:
        raise CustomError(ErrorCode.ToolNotFoundError)

    tool.status = status
    await tool.save()

    return response_ok({"message": "工具状态更新成功"})


@tool_management_bp.post("/delete/version")
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

    tool = await Tool.filter(name=name, version=version, deleted=False).first()
    if not tool:
        raise CustomError(ErrorCode.ToolAlreadyDeletedError)

    if tool.status == 1:
        raise CustomError(ErrorCode.ToolHasScriptError)

    tool_path = os.path.join(TOOLS_DIR, name, version)
    if os.path.exists(tool_path):
        await del_dirs(tool_path)

    tool.deleted = True
    tool.updater = request.ctx.session['account_info'].username
    await tool.save()

    tool_actions = await ToolAction.filter(tool_name=name, version_number=version, deleted=False).all()
    if tool_actions:
        for action in tool_actions:
            action.deleted = True
            await action.save()

    return response_ok({"message": "工具删除成功"})


@tool_management_bp.post("/delete/all")
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

    tools = await Tool.filter(name=name, deleted=False).all()
    if not tools:
        raise CustomError(ErrorCode.ToolAlreadyDeletedError)

    for tool in tools:
        if tool.status == 1:
            raise CustomError(ErrorCode.ToolHasScriptError)

        tool.deleted = True
        tool.updater = request.ctx.session['account_info'].username
        await tool.save()

    tool_path = os.path.join(TOOLS_DIR, name)

    if os.path.exists(tool_path):
        await del_dirs(tool_path)

    tool_actions = await ToolAction.filter(tool_name=name, deleted=False).all()
    if tool_actions:
        for action in tool_actions:
            action.deleted = True
            await action.save()

    tool_resource_models = await ToolResourcesModel.filter(tool_name=name, deleted=False).all()
    if tool_resource_models:
        for resource_model in tool_resource_models:
            resource_model.deleted = True
            await resource_model.save()

    tool_resources = await ToolResources.filter(tool_name=name, deleted=False).all()
    if tool_resources:
        for resource in tool_resources:
            resource.deleted = True
            await resource.save()

    action_permissions = await ActionPermissions.filter(tool_name=name, deleted=False).all()
    if action_permissions:
        for permission in action_permissions:
            permission.deleted = True
            await permission.save()

    return response_ok({"message": "全部工具删除成功"})


@tool_management_bp.post("/resource/model")
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


@tool_management_bp.post("/resource/add")
@requires_permission("tool:c")
async def add_tool_resource(request):
    """
    新增工具资源（需要工具创建权限）

    query参数:
        - tool_name: 工具名称    str
        - name: 资源名称    str
        - description: 资源描述    str
        - product_supplier: 供应商    str    可选
        - product_name: 产品名称    str    可选
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
    description = request.json.get("description")
    product_supplier = request.json.get("product_supplier", None)
    product_name = request.json.get("product_name", None)
    resources = request.json.get("resources")
    if not tool_name or not resources or not name or not description:
        raise CustomError(ErrorCode.ParameterError)

    existing_resource = await ToolResources.get_or_none(name=name, deleted=False)
    if existing_resource:
        raise CustomError(ErrorCode.ResourcesNameExistsError)

    await ToolResources.create(tool_name=tool_name, name=name, description=description,
                               product_supplier=product_supplier,
                               product_name=product_name, resources=resources,
                               creator=request.ctx.session['account_info'].username
                               , updater=request.ctx.session['account_info'].username)

    return response_ok({"message": "工具资源新增成功"})


@tool_management_bp.post("/resource/detailed")
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

    resources = await ToolResources.filter(tool_name=tool_name, deleted=False).values()
    if not resources:
        raise CustomError(ErrorCode.ResourcesNotFoundError)

    return response_ok(resources)


@tool_management_bp.post("/resource/update")
@requires_permission("tool:u")
async def update_tool_resource(request):
    """
    更新工具资源（需要工具更新权限）

    query参数:
        - tool_name: 工具名称    str\
        - name: 资源名称    str  不可修改，修改其他值的凭证
        - description: 资源描述    str    可选
        - product_supplier: 供应商    str    可选
        - product_name: 产品名称    str    可选
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
    description = request.json.get("description", None)
    product_supplier = request.json.get("product_supplier", None)
    product_name = request.json.get("product_name", None)
    resources = request.json.get("resources", None)
    if not tool_name or not name:
        raise CustomError(ErrorCode.ParameterError)

    tool_resource = await ToolResources.filter(tool_name=tool_name, name=name, deleted=False).first()

    if not tool_resource:
        raise CustomError(ErrorCode.ResourcesNotFoundError)

    if description:
        tool_resource.description = description
    if product_supplier:
        tool_resource.product_supplier = product_supplier
    if product_name:
        tool_resource.product_name = product_name
    if resources:
        tool_resource.resources = resources

    tool_resource.updater = request.ctx.session['account_info'].username
    await tool_resource.save()
    return response_ok({"message": "工具资源更新成功"})


@tool_management_bp.post("/resource/delete")
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

    tool_resource = await ToolResources.filter(tool_name=tool_name, name=name, deleted=False).first()

    if not tool_resource:
        raise CustomError(ErrorCode.ResourcesNotFoundError)

    tool_resource.deleted = True
    tool_resource.updater = request.ctx.session['account_info'].username
    await tool_resource.save()

    return response_ok({"message": "工具资源删除成功"})


@tool_management_bp.post("/download")
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
    tool = await Tool.filter(id=tool_id, deleted=False).first()
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


@tool_management_bp.post("/action/permissions/add")
@requires_permission("tool:u")
async def add_permission(request):
    """
    新增工具动作权限（需要工具更新权限）

    query参数:
        - name: 工具名称    str
        - actions: 动作名称    list    可选，默认all
        - users_id: 用户ID列表    list
        - roles_id: 角色ID列表    list
    """
    name = request.json.get("name")
    actions = request.json.get("actions", ["all"])
    users_id = request.json.get("users_id", [])
    roles_id = request.json.get("roles_id", [])
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
        permission.users_id = list(set(permission.users_id or []) | set(users_id))
        permission.roles_id = list(set(permission.roles_id or []) | set(roles_id))

    await ActionPermissions.bulk_update(permissions, fields=["users_id", "roles_id"])

    return response_ok({"message": "工具动作权限新增成功"})


@tool_management_bp.post("/action/permissions/delete")
@requires_permission("tool:u")
async def delete_permission(request):
    """
    删除工具动作权限（需要工具更新权限）

    query参数:
        - name: 工具名称    str
        - actions: 动作名称    list    可选，默认all
        - users_id: 用户ID列表    list
        - roles_id: 角色ID列表    list
    """
    name = request.json.get("name")
    actions = request.json.get("actions", ["all"])
    users_id = request.json.get("users_id", [])
    roles_id = request.json.get("roles_id", [])
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

    # 删除 users_id 和 roles_id
    for permission in permissions:
        permission.users_id = list(set(permission.users_id or []) - set(users_id))
        permission.roles_id = list(set(permission.roles_id or []) - set(roles_id))

    await ActionPermissions.bulk_update(permissions, fields=["users_id", "roles_id"])

    return response_ok({"message": "工具动作权限删除成功"})


@tool_management_bp.post("/action/permissions/select")
@requires_permission("tool:r")
async def select_permission_all(request):
    """
    获取工具全部动作权限（需要工具查看权限）

    query参数:
        - name: 工具名称    str
        - actions: 动作名称    list    可选，默认all
    """
    name = request.json.get("name")
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
        permission_list.append(permission.json)
    return response_ok(permission_list)


@tool_management_bp.post("/action/permissions/judge")
@requires_permission("tool:r")
async def judge_permission(request):
    """
    判断用户是否有工具动作权限（需要工具查看权限）

    query参数:
        - actions: list    工具下的动作名称列表
            [
                {
                    "name": ["action1", "action2"],
                }
            ]
    """

    actions = request.json.get("actions", [])
    if not actions or not isinstance(actions, list):
        raise CustomError(ErrorCode.ParameterError)

    user = request.ctx.session['account_info']
    user_id = user.id

    if not user:
        raise CustomError(ErrorCode.AccountNotFound)

    role_ids = {role.id for role in user.roles}

    tool_action_map = {}
    for action in actions:
        for tool_name, action_list in action.items():
            if not isinstance(action_list, list):
                raise CustomError(ErrorCode.ParameterError)
            tool_action_map.setdefault(tool_name, []).extend(action_list)

    all_permissions = await ActionPermissions.filter(
        tool_name__in=tool_action_map.keys(),
        action_name__in=[a for actions in tool_action_map.values() for a in actions],
        deleted=False
    ).all()

    permission_lookup = {f"{p.tool_name}_{p.action_name}": p for p in all_permissions}

    permission_dict = {}
    for tool_name, action_list in tool_action_map.items():
        for action_name in action_list:
            key = f"{tool_name}_{action_name}"
            permission = permission_lookup.get(key)

            if not permission:
                permission_dict[key] = "false"
                continue

            users_id = set(permission.users_id or [])
            roles_id = set(permission.roles_id or [])

            if user_id in users_id or (role_ids and roles_id & role_ids):
                permission_dict[key] = "true"
            else:
                permission_dict[key] = "false"

    return response_ok(permission_dict)


@tool_management_bp.post("/tag/add")
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


@tool_management_bp.post("/tag/delete")
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


@tool_management_bp.post("/health/check/config")
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
    config = await Config.filter(key=key).first()

    if not config:
        config = await Config.create(key=key, value=orjson.dumps({"cron": cron}), module=["tool"])
        config.deleted = (switch == "off")
    else:
        config.deleted = (switch == "off")
        config.value = orjson.dumps({"cron": cron})
    await config.save()

    return response_ok({"message": "资源健康检查配置修改成功"})


@tool_management_bp.post("/action/input")
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
    action = await ToolAction.filter(id=action_id, deleted=False).first()
    if not action:
        raise CustomError(ErrorCode.ActionNotFoundError)

    input_data = action.action["input"]

    return response_ok(input_data)

