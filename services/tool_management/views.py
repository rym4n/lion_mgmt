# coding=utf-8
import datetime
import hashlib
import json
import os
import zipfile
from io import BytesIO

from sanic import Blueprint, response
from tortoise.expressions import Q
from tortoise.functions import Max

from core.error_code import ErrorCode
from core.response import response_ok
from libs import utils
from libs.error_code.exception import CustomError
from libs.security.authentication import token_decode
from libs.security.authorization import requires_permission
from models import Tool, ToolAction, ToolResources, ToolResourcesModel
from settings import TOOLS_DIR

# 定义 Blueprint
tool_management_bp = Blueprint("tool_management", url_prefix="v1/tool_management")
__all__ = ["tool_management_bp"]

# 预期的文件结构
EXPECTED_STRUCTURE = {
    "required_files": ["config.json", "readme.md"]
}


@tool_management_bp.post("/upload")
@requires_permission("tool:c")
async def upload_and_extract_zip(request):
    """
    上传工具接口（需要工具创建权限）
    请求参数:
        - file: ZIP 文件 (multipart/form-data)
    """
    creator = token_decode(request).get("name")
    if "file" not in request.files:
        return response_ok({"error": "No file provided"})

    # 获取 ZIP 文件
    zip_file = request.files["file"][0]

    config_data, version, tool_name = await unzip(zip_file.body, creator)
    await ToolAction.create(tool_name=config_data.get("name"), version_number=version,
                            action=config_data.get("actions"))
    configuration = await ToolResourcesModel.filter(tool_name=tool_name, deleted=False).first().values("resources")
    if not configuration or configuration["resources"] != config_data.get("configuration"):
        await ToolResourcesModel.create(tool_name=tool_name, resources=config_data.get("configuration"))

    return response_ok({"message": "工具上传成功"})


async def unzip(zip_data, creator):
    """
    解压 ZIP 文件，直接从文件流读取，并进行文件结构校验
    """
    zip_buffer = BytesIO(zip_data)

    hash_func = getattr(hashlib, "md5", None)
    if hash_func is None:
        raise ValueError("Unsupported hash type. Use 'md5', 'sha1', 'sha256', or 'sha512'.")

    hasher = hash_func()
    zip_buffer.seek(0)  # 确保从头开始读取数据
    for chunk in iter(lambda: zip_buffer.read(4096), b""):
        hasher.update(chunk)

    with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
        files_info = zip_ref.infolist()

        actual_files = {os.path.basename(f.filename) for f in files_info if not f.is_dir()}
        py_files_dict = {os.path.basename(f.filename): f for f in files_info if f.filename.endswith(".py")}
        config_path = next((f for f in files_info if os.path.basename(f.filename) == "config.json"), None)

        if not config_path:
            raise CustomError(ErrorCode.MissingConfigFileError)

        missing_files = set(EXPECTED_STRUCTURE["required_files"]) - actual_files
        if missing_files:
            raise CustomError(ErrorCode.MissingConfigFileError)

        with zip_ref.open(config_path) as config_file:
            config_data = json.loads(config_file.read().decode("utf-8"))

        name = config_data.get("name")
        py_file_name = config_data.get("jar")
        class_name_list = [action.get("class_name") for action in config_data.get("actions", [])]

        if not py_file_name:
            raise CustomError(ErrorCode.ConfigFileError)

        if py_file_name not in py_files_dict:
            raise CustomError(ErrorCode.MissingPyFileError)

        with zip_ref.open(py_files_dict[py_file_name]) as py_file:
            py_data = py_file.read().decode("utf-8")

        if any(f"def {class_name}(" not in py_data for class_name in class_name_list):
            raise CustomError(ErrorCode.PythonFileError)

        configuration = await ToolResourcesModel.filter(tool_name=name, deleted=False).first().values("resources")
        if configuration and configuration["resources"] != config_data.get("configuration"):
            raise CustomError(ErrorCode.ResourceError)

        hash_values = await Tool.filter(name=name, deleted=False).all().values("hash_value")
        hash_set = {entry["hash_value"] for entry in hash_values}
        if hash_values and hasher.hexdigest() in hash_set:
            raise CustomError(ErrorCode.ToolAlreadyExists)

        latest_tool = await Tool.filter(name=name).order_by("-ctime").first()
        today_version = datetime.datetime.today().strftime("%Y%m%d")
        version = str(int(latest_tool.version) + 1) if latest_tool and latest_tool.version.startswith(
            today_version) else f"{today_version}001"

        extra_path = os.path.join(TOOLS_DIR, name, version)
        os.makedirs(extra_path, exist_ok=True)

        await utils.unzip(zip_data, extra_path)

        config_parent_dir = os.path.dirname(config_path.filename)
        save_path = os.path.join(extra_path, config_parent_dir).replace("\\", "/")

        tool = await Tool.create(
            name=name, version=version, path=save_path,
            description=config_data.get("description"),
            creator=creator, updater=creator,
            hash_value=hasher.hexdigest()
        )

        return config_data, version, tool.name


@tool_management_bp.post("/all")
@requires_permission("tool:r")
async def all_tools(request):
    """
    获取所有工具列表（仅显示每个工具的最新版本，并对工具名称进行模糊搜索）

    query参数:
        - page: 页码（默认1）     int
        - size: 每页数量（默认10）     int
        - name: 工具名称（可选，模糊搜索）   str
    """
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 10))
    name_filter = request.json.get("name", "").strip()

    subquery = Tool.filter(deleted=False).annotate(max_version=Max("version")).group_by("name")
    if name_filter:
        subquery = subquery.filter(name__icontains=name_filter)
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

    return response_ok(tools)


@tool_management_bp.post("/actions")
@requires_permission("tool:r")
async def tool_actions(request):
    """
    获取工具的所有动作列表（需要工具查看权限）

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

    return response_ok({"actions": actions})


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

    tool.deleted = True
    tool.updater = token_decode(request).get("name")
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
        tool.updater = token_decode(request).get("name")
        await tool.save()

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
        raise CustomError(ErrorCode.ResourcesNotFoundError)

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

    existing_resource = await ToolResources.get_or_none(name=name)
    if existing_resource:
        raise CustomError(ErrorCode.ResourcesNameExistsError)

    await ToolResources.create(tool_name=tool_name, name=name, description=description,
                               product_supplier=product_supplier,
                               product_name=product_name, resources=resources, creator=token_decode(request).get("name")
                               , updater=token_decode(request).get("name"))

    return response_ok({"message": "工具资源新增成功"})


@tool_management_bp.post("/resource/detailed")
@requires_permission("tool:r")
async def tool_resources_detailed(request):
    """
    获取工具的资源详情（需要工具查看权限）

    query参数:
        - name: 工具名称    str
    """
    tool_name = request.json.get("id")
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

    tool_resource.updater = token_decode(request).get("name")
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
    tool_resource.updater = token_decode(request).get("name")
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

    zip_path = os.path.abspath(os.path.join(tool.path, f"SourceFile.zip"))

    tool_name = tool.name
    version = tool.version

    if not os.path.exists(zip_path):
        return CustomError(ErrorCode.ToolNotFoundError)

    return await response.file(zip_path, filename=f"{tool_name}_{version}.zip", mime_type="application/zip")
