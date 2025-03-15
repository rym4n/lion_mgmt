import os

from sanic import Blueprint
from sanic.log import logger
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import SystemCustomConfiguration, SystemStatus, AuditLog

system_bp = Blueprint('system', url_prefix='v1/system')
_all__ = ["system_bp"]

UPLOAD_FOLDER = "uploads"  # 本地存储目录
os.makedirs(UPLOAD_FOLDER, exist_ok=True)  # 确保目录存在


@system_bp.post("/upload")
async def upload_image(request):
    """
    保存系统定制配置（如果 system_version 存在则更新，否则创建）

    请求参数：
        copyright: 版权,
        system_name: 系统名,
        system_version: 系统版本,
        background: 背景图
        webico: 网页ico
        logo: logo
    """
    try:
        copyright_text = request.form.get("copyright")
        system_name = request.form.get("system_name")
        system_version = request.form.get("system_version")

        if not system_version:
            raise CustomError(ErrorCode.ParameterError)

        # 处理文件上传
        file_paths = {}
        for field in ["background", "webico", "logo"]:
            file = request.files.get(field)
            if file:
                filename = f"{system_version}_{file.name}"
                file_path = os.path.join(UPLOAD_FOLDER, filename)

                with open(file_path, "wb") as f:
                    f.write(file.body)

                file_paths[field] = file_path
            else:
                file_paths[field] = None  # 如果文件未上传，则存 None

        async with in_transaction():
            # 先查询是否已存在相同 system_version 的记录
            existing_config = await SystemCustomConfiguration.filter(system_version=system_version).first()

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

                logger.info(f"Client {request.client_ip} updated system configuration {existing_config.id}.")
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

                logger.info(f"Client {request.client_ip} created new system configuration {system_config.id}.")
                return response_ok({"message": "Configuration created successfully", "id": system_config.id})

    except KeyError as e:
        logger.error(f"Missing key in request data: {e}")
        raise CustomError(ErrorCode.ParameterError)

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise CustomError(ErrorCode.ServerError)


@system_bp.post("/latest")
async def get_latest_config(request):
    """
    获取最新的系统定制配置

    无请求参数，直接调用，返回最新的系统定制配置
    """
    try:
        latest_config = await SystemCustomConfiguration.all().order_by("-utime").first()

        if latest_config:
            return response_ok(latest_config.json)
        else:
            raise CustomError(ErrorCode.SystemDataNotFound)

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return CustomError(ErrorCode.ServerError)


@system_bp.post("/status/all")
async def get_system_status(request):
    """
    获取全部系统状况

    无需参数直接请求则返回全部系统状况数据
    """
    data = await SystemStatus.all().values()
    return response_ok(data)


@system_bp.post("/status/add")
async def add_system(request):
    """
    添加新的系统信息

    请求参数：
        systemname：系统名
        instancename：实例名
        uptime：启动时间
        checkTime：检查时间
        status：系统状态
    """
    systemname = request.json.get("systemname")
    instancename = request.json.get("instancename")
    uptime = request.json.get("uptime")
    checkTime = request.json.get("checkTime")
    status = request.json.get("status")
    async with in_transaction():
        systemstatus = await SystemStatus.create(systemname=systemname, instancename=instancename, uptime=uptime,
                                                 checkTime=checkTime, status=status)
    logger.info(f"Client {request.client_ip} has added a new role {systemstatus.id}.")
    return response_ok(systemstatus.json)


@system_bp.post("/status/update")
async def up_system(request):
    """
    添加新的系统信息

    请求参数：
        system_id：系统id
        systemname：系统名
        instancename：实例名
        uptime：启动时间
        checkTime：检查时间
        status：系统状态
    """
    system_id = request.json.get("system_id")
    systemname = request.json.get("systemname")
    instancename = request.json.get("instancename")
    uptime = request.json.get("uptime")
    checkTime = request.json.get("checkTime")
    status = request.json.get("status")
    system = await SystemStatus.filter(id=system_id, deleted=False).first()
    if not system:
        raise CustomError(ErrorCode.NotFound)
    if systemname:
        system.systemname = systemname
    if instancename:
        system.instancename = instancename
    if uptime:
        system.uptime = uptime
    if checkTime:
        system.checkTime = checkTime
    if status:
        system.status = status
    await system.save()
    return response_ok(system.json)


@system_bp.post("/status/delete")
async def del_system(request):
    """
    删除具体的系统

    请求参数：
        system_id：系统id
    """
    system_id = request.json.get("system_id")
    system = await SystemStatus.filter(id=system_id, deleted=False).first()
    if not system:
        raise CustomError(ErrorCode.NotFound)
    system.deleted = True
    await system.save()
    return response_ok("系统删除成功")


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
    data = await AuditLog.filter(**filters).offset(offset).limit(limit).values("id", "username", "ctime", "action", "module", "result", "client_ip", "client_ua", "uri")

    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": data
    })
