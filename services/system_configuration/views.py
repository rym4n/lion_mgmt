import os

from sanic import Blueprint
from sanic.log import logger
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.utils import get_ip
from models import System_custom_configuration

system_bp = Blueprint('system', url_prefix='system')
_all__ = ["system_bp"]

UPLOAD_FOLDER = "uploads"  # 本地存储目录
os.makedirs(UPLOAD_FOLDER, exist_ok=True)  # 确保目录存在


@system_bp.route("/upload", methods=["POST"])
async def upload_image(request):
    """保存系统定制配置（如果 system_version 存在则更新，否则创建）"""
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
            existing_config = await System_custom_configuration.filter(system_version=system_version).first()

            if existing_config:
                # 存在则更新
                update_fields = {
                    "copyright": copyright_text,
                    "system_name": system_name
                }
                for field in ["background", "webico", "logo"]:
                    if file_paths[field]:  # 只更新上传了的文件
                        update_fields[field] = file_paths[field]

                await System_custom_configuration.filter(system_version=system_version).update(**update_fields)

                logger.info(f"Client {get_ip(request)} updated system configuration {existing_config.id}.")
                return response_ok({"message": "Configuration updated successfully", "id": existing_config.id})

            else:
                # 不存在则创建
                system_config = await System_custom_configuration.create(
                    background=file_paths["background"],
                    webico=file_paths["webico"],
                    logo=file_paths["logo"],
                    copyright=copyright_text,
                    system_name=system_name,
                    system_version=system_version
                )

                logger.info(f"Client {get_ip(request)} created new system configuration {system_config.id}.")
                return response_ok({"message": "Configuration created successfully", "id": system_config.id})

    except KeyError as e:
        logger.error(f"Missing key in request data: {e}")
        raise CustomError(ErrorCode.ParameterError)

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise CustomError(ErrorCode.ServerError)


@system_bp.route("/latest", methods=["GET"])
async def get_latest_config(request):
    """获取最新的系统定制配置"""
    try:
        latest_config = await System_custom_configuration.all().order_by("-utime").first()

        if latest_config:
            return response_ok(latest_config.json)
        else:
            raise CustomError(ErrorCode.SystemDataNotFound)

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return CustomError(ErrorCode.ServerError)


