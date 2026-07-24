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
from libs.security.authorization import (
    action_permission_hits,
    check_action_execute_permission,
    check_permissions,
    is_superadmin,
    load_license,
    requires_permission,
)
from libs.utils import gen_run_id
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
from services.digital_employee.lion_agent_adapter import stream_prompt_events, workspace_id_for
from services.digital_employee.lion_agent_client import LionAgentClient
from settings import TASK_TOPIC, TZ

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

TERMINAL_RUNLOG_STATUSES = {-1, 0, 3}
IN_PROGRESS_RESULT = "执行中"

TOOL_VIEW_PERMISSION = "tool:r"
TOOL_HEALTH_REPORT_PERMISSION = "tool:health:r"
TOOL_HEALTH_CONFIG_PERMISSION = TOOL_HEALTH_REPORT_PERMISSION
TOOL_UPLOAD_PERMISSION = "tool:upload:c"
TOOL_DELETE_APP_PERMISSION = "tool:app:d"
TOOL_CREATE_RESOURCE_PERMISSION = "tool:resource:c"
TOOL_UPDATE_RESOURCE_PERMISSION = "tool:resource:u"
TOOL_DELETE_RESOURCE_PERMISSION = "tool:resource:d"
TOOL_DEBUG_PERMISSION = "tool:debug:r"
TOOL_ENGINE_UPDATE_PERMISSION = "tool:engine:u"

connected_clients = {}


async def _ensure_action_permission(tool_name, action_name, action_description, tenant_id):
    return await ActionPermissions.get_or_create(
        tool_name=tool_name,
        action_name=action_name,
        deleted=False,
        tenant_id=tenant_id,
        defaults={
            "action_description": action_description,
            "roles": [],
            "users": [],
        },
    )


def _should_reject_tool_version_delete(tool):
    return bool(tool.maintain)


async def _apply_tool_group_internal_flags(tools, tenant_id):
    tool_names = sorted({tool["name"] for tool in tools})
    if not tool_names:
        return

    internal_tool_names = set(
        await Tool.filter(
            name__in=tool_names,
            internal=True,
            tenant_id=tenant_id,
        ).values_list("name", flat=True)
    )
    for tool in tools:
        tool["internal"] = tool["name"] in internal_tool_names


async def _lock_tool_versions(tool_name, tenant_id):
    return await Tool.filter(
        name=tool_name,
        tenant_id=tenant_id,
    ).select_for_update().order_by("id").all()


def _runlog_stream_signature(log):
    return (
        getattr(log, "utime", None),
        getattr(log, "status", None),
        getattr(log, "result", None),
        getattr(log, "end_time", None),
    )


def _should_stream_runlog(log, sent_versions):
    signature = _runlog_stream_signature(log)
    if sent_versions.get(log.id) == signature:
        return False

    sent_versions[log.id] = signature
    return True


def _is_terminal_runlog(log):
    return getattr(log, "status", None) in TERMINAL_RUNLOG_STATUSES and getattr(log, "result", None) != IN_PROGRESS_RESULT

APP_DEV_DOCS_DIR = "docs"
MAX_APP_DEV_DOC_SIZE = 20 * 1024 * 1024
APP_DEV_DOC_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".json",
    ".md",
    ".pdf",
    ".txt",
    ".xls",
    ".xlsx",
    ".yaml",
    ".yml",
}


def _safe_app_dev_doc_filename(filename):
    """清理上传文档文件名，避免目录穿越和非法路径字符。"""
    filename = Path(filename or "").name.strip()
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    filename = filename.strip(" .")
    if not filename:
        filename = f"{uuid.uuid4().hex}.md"
    return filename


def _ensure_path_inside_base(path: Path, base: Path):
    """确保目标路径仍在指定目录内。"""
    resolved_path = path.resolve()
    resolved_base = base.resolve()
    try:
        resolved_path.relative_to(resolved_base)
    except ValueError:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return resolved_path


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _normalize_search_text(value):
    return str(value or "").casefold()


def _tool_matches_search_key(tool, key):
    normalized_key = _normalize_search_text(key)
    if not normalized_key:
        return True

    searchable_values = [
        tool.get("name"),
        tool.get("description"),
    ]

    return any(normalized_key in _normalize_search_text(value) for value in searchable_values)


def _action_matches_search_key(action, key):
    normalized_key = _normalize_search_text(key)
    if not normalized_key or not isinstance(action, dict):
        return False
    return normalized_key in _normalize_search_text(action.get("func")) or normalized_key in _normalize_search_text(
        action.get("name")
    )


def _build_latest_tool_condition(tool_pairs):
    if not tool_pairs:
        return Q()
    conditions = [Q(name=pair["name"], ctime=pair["latest_ctime"]) for pair in tool_pairs]
    return Q(*conditions, join_type=Q.OR)


def _filter_latest_tool_pairs_by_search_key(tool_pairs, latest_tools, action_tool_names, key):
    matched_tool_names = {
        tool["name"]
        for tool in latest_tools
        if _tool_matches_search_key(tool, key)
    }
    matched_tool_names.update(action_tool_names)
    return [pair for pair in tool_pairs if pair["name"] in matched_tool_names]


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
@requires_permission(TOOL_UPLOAD_PERMISSION)
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


# 资源 data_type 兼容变更白名单：旧类型 -> 允许的新类型集合。
# 仅放行安全增强型变更；其它类型改动（含 password -> string 安全降级）继续拦截。
_COMPATIBLE_RESOURCE_TYPE_CHANGES = {
    "string": {"password"},
}


def _is_compatible_type_change(old_type, new_type) -> bool:
    """资源 data_type 变化是否为非破坏性兼容变更。"""
    if old_type == new_type:
        return True
    return new_type in _COMPATIBLE_RESOURCE_TYPE_CHANGES.get(old_type, set())


async def _resource_keys_in_use(tool_name: str, tenant_id: str) -> set[str]:
    """返回当前租户下该工具所有资源实例实际持有的 key 集合。

    用于判断“删除某个资源 key”是否会影响存量实例：
    只要任意实例的 resources 快照里还带着该 key，就视为仍被使用。
    """
    used: set[str] = set()
    instances = await ToolResources.filter(tool_name=tool_name, deleted=False, tenant_id=tenant_id).all()
    for inst in instances:
        resources = inst.resources_json
        if isinstance(resources, dict):
            used.update(resources.keys())
    return used


async def _rewrite_instance_data_types(tool_name: str, tenant_id: str, type_migrations: dict[str, str]) -> None:
    """把存量资源实例内联的 data_type 同步为模板的新类型。

    脱敏逻辑（ToolResources.json）读的是实例内联的 data_type，不是模板。
    模板放行 string -> password 后若不回写实例，存量 token 会继续明文展示。
    type_migrations: {resource_key: new_data_type}
    """
    if not type_migrations:
        return
    instances = await ToolResources.filter(tool_name=tool_name, deleted=False, tenant_id=tenant_id).all()
    for inst in instances:
        resources = inst.resources_json
        if not isinstance(resources, dict):
            continue
        changed = False
        for key, new_type in type_migrations.items():
            field = resources.get(key)
            if isinstance(field, dict) and field.get("data_type") != new_type:
                field["data_type"] = new_type
                changed = True
        if changed:
            # EncryptedTextField 接受 JSON 字符串；写入时自动加密
            inst.resources = orjson.dumps(resources).decode()
            await inst.save(update_fields=["resources", "utime"])


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
        await _ensure_action_permission(
            tool_name,
            action["func"],
            action.get("name") or "",
            tenant_id,
        )
    await _ensure_action_permission(
        tool_name,
        "all",
        "动作全部动作权限",
        tenant_id,
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
        # 在覆盖模板前，先收集需要回写到存量实例的 data_type 兼容变更
        # （脱敏读的是实例内联 data_type，不是模板）
        old_resources = configuration.resources or {}
        new_resources = config_data.get("resource") or {}
        type_migrations: dict[str, str] = {}
        if isinstance(old_resources, dict) and isinstance(new_resources, dict):
            for key, new_field in new_resources.items():
                old_field = old_resources.get(key)
                if not isinstance(old_field, dict) or not isinstance(new_field, dict):
                    continue
                old_type = old_field.get("data_type")
                new_type = new_field.get("data_type")
                if old_type != new_type and _is_compatible_type_change(old_type, new_type):
                    type_migrations[key] = new_type

        if configuration.health_check != 1 and health_check == 1:
            configuration.health_check = health_check
            configuration.version = version
        configuration.resources = config_data.get("resource")
        # 模板写库与实例 data_type 回写放同一事务，避免中间态
        async with in_transaction():
            await configuration.save()
            if type_migrations:
                await _rewrite_instance_data_types(tool_name, tenant_id, type_migrations)

    return {
        "tool": zip_filename,
        "msg": msg,
        "tool_name": tool_description,
        "name": tool_name,
        "version": version,
    }


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


def generate_tool_version(file_hash, tenant_id):
    """基于文件 hash 和租户 ID 生成租户隔离的版本号。"""
    version_hasher = hashlib.sha1()
    version_hasher.update(file_hash.encode())
    version_hasher.update(b":tenant_id:")
    version_hasher.update(str(tenant_id or "").encode())
    return version_hasher.hexdigest()


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
                old_resources = configuration["resources"] or {}
                new_resources = config_data.get("resource", {}) or {}
                for key in new_resources.keys():
                    # 新增必填资源：无法为存量实例自动补值，拦截
                    if key not in old_resources.keys() and new_resources[key].get("required") is True:
                        return (
                            config_data,
                            version,
                            tool_name,
                            tool_description,
                            "工具资源不一致，请新建工具或删除原工具后再次上传",
                        )
                    # 已有 key 的类型变化：仅放行安全增强型（string -> password），
                    # 其余类型改动（含 password -> string 安全降级）继续拦截
                    if key in old_resources.keys():
                        old_type = old_resources[key].get("data_type")
                        new_type = new_resources[key].get("data_type")
                        if new_type != old_type and not _is_compatible_type_change(old_type, new_type):
                            return (
                                config_data,
                                version,
                                tool_name,
                                tool_description,
                                "工具资源不一致，请新建工具或删除原工具后再次上传",
                            )
                # 删除资源 key：仅当该工具在当前租户下无实例仍持有该 key 时才允许
                removed_keys = set(old_resources.keys()) - set(new_resources.keys())
                if removed_keys:
                    used_keys = await _resource_keys_in_use(name, tenant_id)
                    blocked = removed_keys & used_keys
                    if blocked:
                        return (
                            config_data,
                            version,
                            tool_name,
                            tool_description,
                            f"资源 {', '.join(sorted(blocked))} 仍被已有实例使用，无法删除，请先清理相关资源实例",
                        )

            file_hash = hasher.hexdigest()
            version = generate_tool_version(file_hash, tenant_id)

            if await Tool.filter(name=name, deleted=False, tenant_id=tenant_id, version=version).exists():
                return config_data, version, tool_name, tool_description, "工具已存在"

            suffix = logo_name.split(".")[-1]
            save_path = await utils.upload_to_s3(zip_data, "system", f"{tool_name}/{version}.zip", "tools")
            logo_path = await utils.upload_to_s3(logo_data, "user", f"{tool_name}.{suffix}", "tools")
            if not save_path or not logo_path:
                if save_path:
                    await utils.delete_from_s3("system", f"{tool_name}/{version}.zip", "tools")
                if logo_path:
                    await utils.delete_from_s3("user", f"{tool_name}.{suffix}", "tools")
                return config_data, version, tool_name, tool_description, "工具包上传失败，请检查文件存储服务"

            maintenance_mode = await redis_client.get(token_id)
            tool = await Tool.create(
                name=name,
                version=version,
                path=save_path,
                logo_path=logo_path,
                description=config_data.get("description"),
                creator=creator,
                updater=creator,
                hash_value=version,
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
    key = str(req_json.get("key", "") or "").strip()
    available_only = req_json.get("available_only", False)
    search_actions = _as_bool(req_json.get("search_actions"), True)

    # if size > 50:
    #     raise CustomError(ErrorCode.REQUEST_TOO_MANY_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    subquery = Tool.filter(deleted=False, tenant_id=tenant_id).annotate(latest_ctime=Max("ctime")).group_by("name")
    if req_json.get("tag"):
        subquery = subquery.filter(tag__contains=[req_json.get("tag")])
    subquery_results = await subquery.values("name", "latest_ctime")
    if key and subquery_results:
        tool_names_from_actions = set()

        if search_actions:
            actions = await ToolAction.filter(deleted=False, tenant_id=tenant_id).values("tool_name", "version_number", "action")
            for act in actions:
                if _action_matches_search_key(act["action"], key):
                    tool_names_from_actions.add(act["tool_name"])

        tool_names = [pair["name"] for pair in subquery_results]
        tool_rows_query = Tool.filter(name__in=tool_names, deleted=False, tenant_id=tenant_id)
        if req_json.get("tag"):
            tool_rows_query = tool_rows_query.filter(tag__contains=[req_json.get("tag")])
        matching_tool_rows = await tool_rows_query.values("name", "description", "main", "tag")
        subquery_results = _filter_latest_tool_pairs_by_search_key(
            subquery_results, matching_tool_rows, tool_names_from_actions, key
        )
    if not subquery_results:
        return response_ok({"tools": [], "total": 0, "page": page, "size": size, "total_pages": 0})

    # 如果需要筛选可用工具
    if available_only:
        # 获取所有工具的资源模型
        tool_names = [pair["name"] for pair in subquery_results]
        tool_resources_model = await ToolResourcesModel.filter(tool_name__in=tool_names, deleted=False, tenant_id=tenant_id).values("tool_name", "status")

        # 构建工具资源状态映射
        tool_resource_map = {tr["tool_name"]: tr["status"] for tr in tool_resources_model}

        # 获取所有实际存在的资源（用于判断status=1的工具是否有资源）
        actual_resources = await ToolResources.filter(tool_name__in=tool_names, deleted=False, tenant_id=tenant_id).values_list("tool_name", flat=True)
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

    # 使用扁平 OR 条件，避免 reduce(operator.or_, ...) 构造左深 Q 树导致递归深度超限
    condition = _build_latest_tool_condition(subquery_results)

    latest_tools = await Tool.filter(condition, deleted=False, tenant_id=tenant_id).order_by("-utime", "-ctime").offset((page - 1) * size).limit(size).values()
    await _apply_tool_group_internal_flags(latest_tools, tenant_id)
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


async def update_latest_tool_utime(tool_name, tenant_id, maintenance_mode=False):
    latest_tool = await Tool.filter(
        name=tool_name,
        deleted=False,
        maintain=maintenance_mode if maintenance_mode else False,
        tenant_id=tenant_id,
    ).order_by("-ctime").first()
    if not latest_tool:
        return

    latest_tool.utime = datetime.datetime.now(TZ)
    await latest_tool.save(update_fields=["utime"])


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
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    tools = (
        await Tool.filter(
            name=name,
            deleted=False,
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
                    "filter_by_permission": {
                        "type": "boolean",
                        "description": "兼容旧客户端的保留字段，不再过滤动作；资源执行权限由资源详情接口返回。",
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

    actions = await ToolAction.filter(tenant_id=tenant_id, deleted=False).values("action", "id", "tool_name", "version_number")
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
@requires_permission(TOOL_DELETE_APP_PERMISSION)
async def delete_tool(request):
    name = request.json.get("name")
    version = request.json.get("version")
    if not name or not version:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    async with in_transaction():
        tool_versions = await _lock_tool_versions(name, tenant_id)
        active_tools = [item for item in tool_versions if not item.deleted]
        tool = next((item for item in active_tools if item.version == version), None)
        if not tool:
            return response_ok({"message": "工具删除成功"})

        if tool.status == 1:
            raise CustomError(ErrorCode.TOOL_HAS_SCRIPT_ERROR)

        if _should_reject_tool_version_delete(tool):
            raise CustomError(ErrorCode.INTERNAL_DELETE_ERROR)

        if len(active_tools) == 1:
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
@requires_permission(TOOL_DELETE_APP_PERMISSION)
async def delete_tool_all(request):
    name = request.json.get("name")
    if not name:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    async with in_transaction():
        tool_versions = await _lock_tool_versions(name, tenant_id)
        active_tools = [item for item in tool_versions if not item.deleted]
        if any(tool.maintain for tool in active_tools):
            raise CustomError(ErrorCode.INTERNAL_DELETE_ERROR)
        if any(tool.status == 1 for tool in active_tools):
            raise CustomError(ErrorCode.TOOL_HAS_SCRIPT_ERROR)

        for tool in active_tools:
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
@requires_permission(TOOL_CREATE_RESOURCE_PERMISSION)
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
    await update_latest_tool_utime(tool_name, tenant_id, maintenance_mode)

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
                    "name": {"type": "string", "description": "工具名称"},
                    "action_name": {
                        "type": "string",
                        "description": "动作 func，与 filter_by_permission=true 搭配使用，为每个资源计算执行权限",
                    },
                    "filter_by_permission": {
                        "type": "boolean",
                        "description": "为 true 且传入 action_name 时返回全部资源并通过 has_permission 标记执行权限；超管豁免。默认 false。",
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
                                        "id": {"type": "string"},
                                        "ctime": {"type": "string"},
                                        "utime": {"type": "string"},
                                        "tool_name": {"type": "string"},
                                        "name": {"type": "string"},
                                        "engine": {"type": "string"},
                                        "resources": {"type": "object"},
                                        "creator": {"type": "string"},
                                        "updater": {"type": "string"},
                                        "has_permission": {
                                            "type": "boolean",
                                            "description": "当前用户是否有权使用该资源执行所选动作；仅请求权限标记时返回",
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
async def tool_resources_detailed(request):
    tool_name = request.json.get("name")
    action_name = request.json.get("action_name")
    filter_by_permission = bool(request.json.get("filter_by_permission"))
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

    permission_by_resource = None
    # 返回全部资源，并按 (tool, action) 标记资源级授权；超管全部放行。
    if filter_by_permission and action_name:
        user = request.ctx.session.get("account_info")
        if is_superadmin(user, tenant_id=tenant_id):
            permission_by_resource = {str(resource.id): True for resource in resources}
        else:
            user_id = str(user.id)
            user_role_ids = {
                str(role.id)
                for role in (user.roles or [])
                if getattr(role, "tenant_id", None) == tenant_id
            }
            action_perms = await ActionPermissions.filter(
                tool_name=tool_name,
                action_name__in=[action_name, "all"],
                deleted=False,
                tenant_id=tenant_id,
            ).all()
            permission_by_resource = {
                str(resource.id): any(
                    action_permission_hits(
                        permission,
                        str(resource.id),
                        user_id,
                        user_role_ids,
                    )
                    for permission in action_perms
                )
                for resource in resources
            }

    resource_list = []
    for resource in resources:
        resource_data = resource.json
        if permission_by_resource is not None:
            resource_data["has_permission"] = permission_by_resource.get(
                str(resource.id),
                False,
            )
        resource_list.append(resource_data)

    return response_ok(resource_list)


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
@requires_permission(
    TOOL_UPDATE_RESOURCE_PERMISSION,
    TOOL_ENGINE_UPDATE_PERMISSION,
)
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

    engine_requested = engine is not None
    resources_requested = bool(resources)
    if resources_requested:
        await check_permissions(
            request,
            TOOL_UPDATE_RESOURCE_PERMISSION,
        )
    if engine_requested:
        await check_permissions(
            request,
            TOOL_ENGINE_UPDATE_PERMISSION,
        )

    if engine_requested:
        tool_resource.engine = engine

    if resources_requested:
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
    if engine_requested:
        update_fields.append("engine")
    if resources_requested:
        update_fields.append("resources")
    await tool_resource.save(update_fields=update_fields)
    await update_latest_tool_utime(tool_name, tenant_id, maintenance_mode)
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
@requires_permission(TOOL_DELETE_RESOURCE_PERMISSION)
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
@requires_permission(TOOL_UPDATE_RESOURCE_PERMISSION)
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

    async with in_transaction():
        if "all" in actions:
            await _ensure_action_permission(
                name,
                "all",
                "动作全部动作权限",
                tenant_id,
            )
            permission_query = ActionPermissions.filter(
                tool_name=name,
                action_name="all",
                deleted=False,
                tenant_id=tenant_id,
            )
        else:
            permission_query = ActionPermissions.filter(
                tool_name=name,
                action_name__in=actions,
                deleted=False,
                tenant_id=tenant_id,
            )
        permissions = await permission_query.select_for_update().order_by("id").all()

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
        await _ensure_action_permission(
            name,
            "all",
            "动作全部动作权限",
            tenant_id,
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
@requires_permission(TOOL_HEALTH_CONFIG_PERMISSION)
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
@requires_permission(TOOL_HEALTH_REPORT_PERMISSION)
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
@requires_permission(
    TOOL_ENGINE_UPDATE_PERMISSION,
    TOOL_UPDATE_RESOURCE_PERMISSION,
    TOOL_CREATE_RESOURCE_PERMISSION,
)
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
@requires_permission(TOOL_HEALTH_REPORT_PERMISSION)
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
        tenant_id = request.ctx.session.get("tenant_id")
        if message.get("type") == "action" and not await check_action_execute_permission(
            request.ctx.session["account_info"],
            message.get("app_name"),
            message.get("action"),
            message.get("resource"),
            tenant_id,
        ):
            await resp.send("data: 缺少动作资源执行权限，执行失败\n\n")
            await resp.eof()
            return resp
        run_id = gen_run_id()
        message["executor"] = request.ctx.session["account_info"].id
        msg = {"run_type": message["type"], "run_id": run_id, "run_args": message, "tenant_id": tenant_id}
        await aproduce(TASK_TOPIC, msg)
        sent_versions = {}
        expected_action_logs = len((message.get("resource") or "").split()) if message.get("type") == "action" else 0
        for _ in range(60):
            logs = await Runlog.filter(
                run_id=run_id,
                utime__gte=utime,
                deleted=False,
                tenant_id=request.ctx.session.get("tenant_id"),
            ).order_by("utime", "ctime", "id").all()
            if logs:
                for log in logs:
                    if _should_stream_runlog(log, sent_versions):
                        await resp.send(f"data: {orjson.dumps(log.json).decode()}\n\n")
                if message.get("type") == "flow" and any(log.node_type == "end-node" and _is_terminal_runlog(log) for log in logs):
                    break
                if message.get("type") == "action" and len(logs) >= expected_action_logs and all(_is_terminal_runlog(log) for log in logs):
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
@requires_permission(TOOL_DEBUG_PERMISSION)
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
@requires_permission(TOOL_UPDATE_RESOURCE_PERMISSION)
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
@requires_permission(TOOL_HEALTH_REPORT_PERMISSION)
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
    tenant_id = request.ctx.session.get("tenant_id")
    if not keyword:
        return response_ok([])

    resources = await ToolResources.filter(name__icontains=keyword, deleted=False, internal=False, tenant_id=tenant_id).all()
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
    available_tool_names = await Tool.filter(deleted=False, maintain=False, tenant_id=tenant_id).values_list("name", flat=True)

    # 获取所有工具资源模型
    tool_resources = await ToolResourcesModel.filter(deleted=False, tenant_id=tenant_id).all()

    # 构建工具名称到资源状态的映射
    tool_resource_status = {tr.tool_name: tr.status for tr in tool_resources}

    # 获取所有实际存在的资源（用于判断status=1的工具是否有资源）
    actual_resources = await ToolResources.filter(deleted=False, tenant_id=tenant_id, tool_name__in=available_tool_names).values_list("tool_name", flat=True)
    tools_with_resources = set(actual_resources)

    # 获取这些工具的动作（只查询 maintain=False 的工具）
    actions = await ToolAction.filter(deleted=False, tenant_id=tenant_id, tool_name__in=available_tool_names).all()

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
            result.append(
                {
                    "id": str(action.id),
                    "name": action_data.get("name", ""),
                    "description": action_data.get("name", ""),
                    "tool_name": action.tool_name,
                    "version": action.version_number,
                }
            )
        elif tool_status == 1 and action.tool_name in tools_with_resources:
            # status=1 且存在实际资源，认为可用
            action_data = action.action
            result.append(
                {
                    "id": str(action.id),
                    "name": action_data.get("name", ""),
                    "description": action_data.get("name", ""),
                    "tool_name": action.tool_name,
                    "version": action.version_number,
                }
            )

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
@requires_permission(TOOL_HEALTH_CONFIG_PERMISSION)
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
            resource = await ToolResources.get_or_none(tool_name=tool_name, name=resource_name, deleted=False, tenant_id=tenant_id, internal=False)
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
            resource = await ToolResources.get_or_none(tool_name=tool_name, name=resource_name, deleted=False, tenant_id=tenant_id, internal=False)
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
            resource = await ToolResources.get_or_none(tool_name=tool_name, name=resource_name, deleted=False, tenant_id=tenant_id, internal=False)
            if resource:
                resource.status = state
                await resource.save(update_fields=["status"])
                updated_count += 1
                status_counts["unknown"] += 1

    return response_ok(
        {
            "message": "资源健康状态同步成功",
            "updated_count": updated_count,
            "details": status_counts,
        }
    )


def _extract_code_json_from_text(content: str) -> dict | None:
    json_str = None
    matches = re.findall(r"```json\s*\n(.*?)\n```", content, re.DOTALL)
    if matches:
        json_str = matches[-1].strip()
    if not json_str:
        start_positions = [index for index, char in enumerate(content) if char == "{"]
        for start_pos in reversed(start_positions):
            brace_count = 0
            for end_pos in range(start_pos, len(content)):
                if content[end_pos] == "{":
                    brace_count += 1
                elif content[end_pos] == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        potential_json = content[start_pos : end_pos + 1]
                        try:
                            parsed = orjson.loads(potential_json)
                        except orjson.JSONDecodeError:
                            continue
                        if isinstance(parsed, dict) and "config" in parsed and "main" in parsed:
                            json_str = potential_json
                            break
            if json_str:
                break
    if not json_str:
        return None
    try:
        code_data = orjson.loads(json_str)
    except orjson.JSONDecodeError as exc:
        logger.warning(f"解析应用开发代码 JSON 失败: {exc}")
        return None
    if not isinstance(code_data, dict) or "config" not in code_data or "main" not in code_data:
        return None
    for key in ("config", "main"):
        if isinstance(code_data.get(key), str):
            code_data[key] = code_data[key].replace("\\n", "\n").replace("\\t", "\t")
    return code_data


async def _send_browser_sse(resp, payload: dict) -> None:
    await resp.send(f"data: {orjson.dumps(payload).decode()}\n\n")


def _tool_name_from_payload(payload: dict) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return str(data.get("name") or data.get("tool_name") or data.get("tool") or "tool")


@tools_bp.post("/browser/document/upload")
@openapi.definition(
    summary="上传应用开发文档",
    description="上传文档并同步到当前租户的 lion_agent browser workspace（需要工具创建权限）",
    body={
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "文档文件，支持 md/txt/pdf/doc/docx/xls/xlsx/csv/json/yaml",
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
                                    "message": {"type": "string"},
                                    "files": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "filename": {"type": "string"},
                                                "size": {"type": "integer"},
                                                "workspace_path": {"type": "string"},
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
@requires_permission(TOOL_UPLOAD_PERMISSION, TOOL_DEBUG_PERMISSION)
async def upload_browser_document(request):
    """
    上传应用开发文档到当前租户的 lion_agent browser workspace。

    保存位置：
    1. lion_agent workspace docs：当前 browser workspace 可直接读取的目录
    """
    if not request.files or "file" not in request.files:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")
    if not tenant_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    saved_files = []
    extra_files = {}
    for uploaded_file in request.files.getlist("file"):
        original_name = getattr(uploaded_file, "name", None) or getattr(uploaded_file, "filename", None) or ""
        filename = _safe_app_dev_doc_filename(original_name)
        suffix = Path(filename).suffix.lower()
        if suffix not in APP_DEV_DOC_EXTENSIONS:
            raise CustomError(ErrorCode.PARAM_ERROR)

        file_body = uploaded_file.body or b""
        if not file_body or len(file_body) > MAX_APP_DEV_DOC_SIZE:
            raise CustomError(ErrorCode.PARAM_ERROR)

        rel_path = f"{APP_DEV_DOCS_DIR}/{filename}"
        extra_files[rel_path] = {"encoding": "base64", "data": base64.b64encode(file_body).decode("ascii")}

        saved_files.append(
            {
                "filename": filename,
                "size": len(file_body),
                "workspace_path": rel_path,
            }
        )
    await LionAgentClient().sync_workspace(workspace_id_for(tenant_id, "browser"), {"extra": extra_files})

    return response_ok({"message": "文档上传成功", "files": saved_files})


@tools_bp.post("/browser/execute")
@openapi.definition(
    summary="执行智能任务",
    description="通过自然语言执行各类智能任务（浏览器自动化、SSH服务器操作、API调用等），支持会话上下文（需要工具查看权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["task"],
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "任务描述（自然语言）",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "会话 ID（用于保持上下文历史，可选）",
                    },
                },
            }
        }
    },
    response={"200": {"description": "执行成功，返回 SSE 流（包含文本消息、截图数据和生成的代码）"}},
)
@requires_permission(TOOL_DEBUG_PERMISSION)
async def browser_execute(request):
    """
    通过 lion_agent 执行智能任务

    工作流程：
    1. 接收自然语言任务描述
    2. 如果提供 session_id，加载历史上下文
    3. 调用 lion_agent 处理任务
    4. lion_agent 根据任务类型自动调用相应的 skill（浏览器自动化、SSH操作、API调用等）
    5. 实时返回执行进度、结果和截图

    SSE 事件类型：
    - status: starting - 开始处理任务
    - status: processing - lion_agent 正在理解任务
    - status: executing - 正在执行工具
    - status: message - 文本消息流式输出
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
            await _send_browser_sse(resp, {"status": "error", "message": "请求体必须是有效的 JSON"})
            await resp.eof()
            return resp

        task = request.json.get("task")
        session_id = request.json.get("session_id")

        if not task:
            await _send_browser_sse(resp, {"status": "error", "message": "task 参数不能为空"})
            await resp.eof()
            return resp

        # 如果没有提供 session_id，自动生成一个
        if not session_id:
            session_id = str(uuid.uuid4())
            logger.info(f"未提供 session_id，自动生成: {session_id}")

        tenant_id = request.ctx.session.get("tenant_id")

        # 返回 session_id 给客户端，用于后续请求
        await _send_browser_sse(resp, {"status": "starting", "message": "开始处理任务...", "session_id": session_id})

        # 构建完整消息：静态执行规则由 lion_agent 工作区 system_prompt.md 提供
        full_message = f"""<user_task>
{task}
</user_task>"""

        await _send_browser_sse(resp, {"status": "processing", "message": "正在理解任务..."})

        session_key = f"browser:{session_id}"

        logger.info(f"准备调用 lion_agent，session_key={session_key}")
        logger.info(f"消息内容长度: {len(full_message)} 字符")

        accumulated_content = ""
        code_sent = False
        async for event_type, payload in stream_prompt_events(
            workspace_id=workspace_id_for(tenant_id, "browser"),
            session_id=session_key,
            request_id=session_id,
            message=full_message,
        ):
            if event_type == "delta":
                content = str(payload.get("content") or payload.get("delta") or "")
                if not content:
                    continue
                accumulated_content += content
                if not code_sent and (code_data := _extract_code_json_from_text(accumulated_content)):
                    await _send_browser_sse(resp, {"status": "code", "data": code_data})
                    code_sent = True
                    continue
                if not code_sent:
                    await _send_browser_sse(resp, {"status": "message", "content": content})
            elif event_type == "thinking":
                content = str(payload.get("content") or "")
                if content:
                    await _send_browser_sse(resp, {"status": "message", "content": content})
            elif event_type == "tool_call":
                await _send_browser_sse(resp, {"status": "executing", "message": f"正在执行 {_tool_name_from_payload(payload)}..."})
            elif event_type == "progress":
                screenshot = payload.get("screenshot") or payload.get("screenshots")
                if screenshot:
                    await _send_browser_sse(resp, {"status": "screenshot", "data": screenshot})
            elif event_type == "error":
                raise RuntimeError(payload.get("message") or payload)
            elif event_type == "end":
                content = str(payload.get("content") or "")
                if content and not accumulated_content:
                    accumulated_content = content
                    if not code_sent and (code_data := _extract_code_json_from_text(accumulated_content)):
                        await _send_browser_sse(resp, {"status": "code", "data": code_data})
                        code_sent = True
                    elif not code_sent:
                        await _send_browser_sse(resp, {"status": "message", "content": content})

        logger.info(f"lion_agent 调用完成，返回结果长度: {len(accumulated_content)} 字符")
        await _send_browser_sse(resp, {"status": "completed", "message": "任务执行完成"})

    except Exception as e:
        logger.exception("自动化任务执行失败")
        try:
            if resp:
                # 使用 orjson 确保 JSON 安全
                error_data = orjson.dumps({"status": "error", "message": f"执行失败: {str(e)}", "session_id": session_id}).decode()  # 即使失败也返回 session_id
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


@tools_bp.post("/browser/session/delete")
@openapi.definition(
    summary="删除应用智能开发会话",
    description="删除指定应用智能开发会话（需要工具删除权限）",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["session_id"],
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "会话 ID",
                    }
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
@requires_permission(TOOL_DEBUG_PERMISSION)
async def delete_browser_session(request):
    """
    删除浏览器自动化会话

    lion_agent 持有 workspace 会话状态；当前接口保持兼容响应。
    """
    session_id = request.json.get("session_id")
    if not session_id:
        raise CustomError(ErrorCode.PARAM_ERROR)

    return response_ok({"message": "会话删除成功"})
