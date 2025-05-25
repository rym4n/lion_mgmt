import json
import os
from datetime import datetime

import aiofile
import orjson
from sanic import Blueprint
from tortoise.transactions import in_transaction

import settings
from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import strip_timezone_to_seconds
from models import SystemCustomConfiguration, AuditLog
from settings import UPLOAD_FOLDER, BG_NAME

system_bp = Blueprint('system', url_prefix='v1/system')
_all__ = ["system_bp"]


@system_bp.post("/upload")
@requires_permission("system:u,c")
async def upload_image(request):
    """
    保存系统定制配置（如果 system_version 存在则更新，否则创建）  需要系统配置新增和修改权限

    请求参数：
        copyright: 版权,
        system_name: 系统名,
        system_version: 系统版本,
        background: 背景图
        webico: 网页ico
        logo: logo
    """
    copyright_text = request.form.get("copyright")
    system_name = request.form.get("system_name")
    system_version = request.form.get("system_version")

    if not system_version:
        raise CustomError(ErrorCode.ParameterError)

    config_path = os.path.join(UPLOAD_FOLDER, "platform-config.json")

    async with aiofile.AIOFile(config_path, 'r', encoding='utf-8') as f:
        content = await f.read()
    content = json.loads(content)
    content["Version"] = system_version
    content["Content"] = copyright_text
    content["Title"] = system_name

    async with aiofile.AIOFile(config_path, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(content, ensure_ascii=False, indent=2))

    # 处理文件上传
    file_paths = {}
    for field in ["background", "webico", "logo"]:
        file = request.files.get(field)
        filename = ''
        if field == "background":
            filename = f"{BG_NAME}.png"
        elif field == "webico":
            filename = f"favicon.ico"
        elif field == "logo":
            filename = f"logo.svg"
        if file and filename:
            file_path = os.path.join(UPLOAD_FOLDER, filename)

            with open(file_path, "wb") as f:
                f.write(file.body)
            file_path = file_path.replace("\\", "/")
            file_paths[field] = file_path
        else:
            file_paths[field] = None  # 如果文件未上传，则存 None

    async with in_transaction():
        # 先查询是否已存在相同 system_version 的记录
        existing_config = await SystemCustomConfiguration.get_or_none(system_version=system_version)

        if existing_config:
            # 存在则更新
            update_fields = {
                "copyright": copyright_text,
                "system_name": system_name
            }
            for field in ["background", "webico", "logo"]:
                if file_paths[field]:  # 只更新上传了的文件
                    update_fields[field] = file_paths[field]

            await SystemCustomConfiguration.filter(system_version=system_version).update(**update_fields)

            return response_ok({"message": "Configuration updated successfully", "id": existing_config.id})

        else:
            # 不存在则创建
            system_config = await SystemCustomConfiguration.create(
                background=file_paths["background"],
                webico=file_paths["webico"],
                logo=file_paths["logo"],
                copyright=copyright_text,
                system_name=system_name,
                system_version=system_version
            )

            return response_ok({"message": "Configuration created successfully", "id": system_config.id})


@system_bp.post("/latest")
@requires_permission("system:r")
async def get_latest_config(request):
    """
    获取最新的系统定制配置   需要系统配置查看权限

    无请求参数，直接调用，返回最新的系统定制配置
    """
    latest_config = await SystemCustomConfiguration.all().order_by("-utime").first()

    if latest_config:
        return response_ok(latest_config.json)
    else:
        raise CustomError(ErrorCode.SystemDataNotFound)


@system_bp.post("/status/all")
@requires_permission("system:r")
async def get_system_status(request):
    """
    获取全部系统状况    需要系统配置查看权限

    无需参数直接请求则返回全部系统状况数据
    """
    name_map = {'lion-mgmt': 'Web控制台', 'lion-web': 'Nginx服务', 'lion-pg': 'Postgres数据库', 'lion-redis': 'Redis数据库', 'lion-ship': 'ETL服务', 'lion-job': '任务调度服务', 'lion-worker': '执行引擎', 'lion-kafka': 'Kafka消息队列'}
    # 获取文件的修改时间
    status_file = os.path.join(settings.BASE_DIR, "data/status.json")
    if not os.path.exists(status_file):
        raise CustomError(ErrorCode.CheckStatusNotFound)
    modify_time = datetime.strftime(datetime.fromtimestamp(os.path.getmtime(status_file)), "%Y-%m-%d %H:%M:%S")
    async with aiofile.AIOFile(status_file, 'r', encoding='utf-8') as f:
        content = await f.read()
        data = orjson.loads(content)
        for item in data:
            item['name'] = name_map.get(item['name'], item['name'])
            item['start_time'] = strip_timezone_to_seconds(item['start_time'])
            item['check_time'] = modify_time
        return response_ok(data)


@system_bp.post("/audit")
@requires_permission("audit:r")
async def search_audit(request):
    """
    查询审计日志

    请求参数：
        username: 用户名
        start_time: 开始时间
        end_time: 结束时间
        ip: ip,
        page: 页码
        size: 每页条数
    """
    page = int(request.json.get('page', 1))
    size = int(request.json.get('size', 10))
    offset = (page - 1) * size
    limit = size

    username = request.json.get("username")
    start_time = request.json.get("start_time")
    end_time = request.json.get("end_time")
    ip = request.json.get("ip")
    filters = {}
    if start_time:
        filters["ctime__gte"] = start_time
    if end_time:
        filters["ctime__lte"] = end_time
    if ip:
        filters["client_ip"] = ip
    if username:
        filters["username"] = username

    total = await AuditLog.filter(**filters).count()
    data = await AuditLog.filter(**filters).offset(offset).limit(limit)

    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": [item.json for item in data]
    })
