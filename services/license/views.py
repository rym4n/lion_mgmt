# coding=utf-8

import base64
import datetime
import hashlib
import os
import threading
import time

import aiofile
import orjson
from sanic import Blueprint
from sanic import response
from sanic.request import Request

import settings
from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from services import verify_license
from settings import LICENSE_DIR

license_bp = Blueprint('license', url_prefix='v1/license')
_all__ = ["license_bp"]

MENU_DICT = {
    "role_bp": "role",
    "user_bp": "user",
    "event_bp": "event",
    "event_ingestion_bp": "ingestion",
    "var_bp": "var",
    "approve_bp": "approve",
    "tool_management_bp": "tool",
    "workflow_bp": "flow",
    "system_bp": "system",
    "scene_bp": "scene",
    "timed_bp": "timed",
    "license_bp": "lic",
    "warroom_bp": "warroom"
}


def delayed_exit():
    time.sleep(1)
    os._exit(1)  # 强制退出进程


@license_bp.post("/verify")
@requires_permission("lic:c")
async def verify_license_api(request: Request):
    """
    验证license文件

    请求参数：
        - file: license文件
    """
    if "file" not in request.files:
        return response.json({"success": False, "message": "缺少必要字段"}, status=400)

    license_file = request.files.get("file")
    encoded = license_file.body.decode("utf-8")
    json_str = base64.b64decode(encoded.encode("utf-8")).decode("utf-8")
    license_data = orjson.loads(json_str)
    is_valid = verify_license(license_data)
    if is_valid:
        if license_data.get("expireTime"):
            if int(time.time() * 1000) >= license_data.get("expireTime"):
                settings.LICENSE_DATA = "timeout"
                raise CustomError(ErrorCode.LicenseExpired)
            else:
                license_path = hashlib.md5(f"{encoded}".encode()).hexdigest() + ".lic"
                for root, dirs, files in os.walk(LICENSE_DIR):
                    for file in files:
                        full_path = os.path.join(root, file)
                        new_path = os.path.join(root, license_path)
                        async with aiofile.AIOFile(full_path, 'w', encoding='utf-8') as f:
                            await f.write(encoded)
                        if os.path.exists(full_path):
                            os.rename(full_path, new_path)
                        settings.LICENSE_DATA = license_data

                threading.Thread(target=delayed_exit, args=(1,), daemon=True).start()
                return response_ok("上传成功，正在加载配置文件，请稍后！")
    else:
        raise CustomError(ErrorCode.InvalidLicenseError)


@license_bp.post("/get")
@requires_permission("lic:r")
async def get_license(request: Request):
    """
    获取许可文件权限，加载页面

    无需请求参数
    """
    menu_dict = {}

    for root, dirs, files in os.walk(LICENSE_DIR):
        for file in files:
            filename, ext = os.path.splitext(file)
            full_path = os.path.join(root, file)
            async with aiofile.AIOFile(full_path, 'r', encoding='utf-8') as f:
                data = await f.read()
                if filename != hashlib.md5(f"{data}".encode()).hexdigest():
                    menu_dict["license_bp"] = True
                    menu_dict["user_bp"] = True
                    return response_ok(menu_dict)

    if data:
        json_str = base64.b64decode(data.encode("utf-8")).decode("utf-8")
        license_data = orjson.loads(json_str)
        is_valid = verify_license(license_data)
        if is_valid:
            if license_data.get("expireTime"):
                if int(time.time() * 1000) < license_data.get("expireTime"):
                    for item in license_data["menuInfo"]["moduleList"]:
                        module = item["module"]
                        auth = item["auth"]

                        if module not in menu_dict:
                            menu_dict[module] = False
                        if auth and int(item["expireTime"]) > int(time.time() * 1000):
                            menu_dict[module] = True
    menu_dict["license_bp"] = True
    menu_dict["user_bp"] = True

    new_menu_dict = {}
    for key, value in menu_dict.items():
        if key in MENU_DICT:
            new_menu_dict[MENU_DICT[key]] = value

    return response_ok(new_menu_dict)


@license_bp.post("/show")
@requires_permission("lic:r")
async def get_all_license(request: Request):
    """
    获取所有license文件信息用于页面展示
    """
    for root, dirs, files in os.walk(LICENSE_DIR):
        for file in files:
            filename, ext = os.path.splitext(file)
            full_path = os.path.join(root, file)
            async with aiofile.AIOFile(full_path, 'r', encoding='utf-8') as f:
                data = await f.read()
                if filename != hashlib.md5(f"{data}".encode()).hexdigest():
                    raise CustomError(ErrorCode.InvalidLicenseError)

    menu_dict = {}

    if data:
        json_str = base64.b64decode(data.encode("utf-8")).decode("utf-8")
        license_data = orjson.loads(json_str)
        is_valid = verify_license(license_data)
        if is_valid:
            if license_data.get("expireTime"):
                if int(time.time() * 1000) < license_data.get("expireTime"):
                    menu_dict["name"] = license_data["company"]
                    menu_dict["numbering"] = filename
                    menu_dict["version"] = license_data["version"]
                    menu_dict["createTime"] = formatted_time(license_data["createTime"])
                    menu_dict["expireTime"] = formatted_time(license_data["expireTime"])
                    menu_dict["menuInfo"] = {}
                    for item in license_data["menuInfo"]["moduleList"]:
                        name = item["name"]
                        auth = item["auth"]
                        group = item["group"]

                        if auth and int(item["expireTime"]) > int(time.time() * 1000):
                            if name in ["应用数量", "剧本数量限制"]:
                                menu_dict["menuInfo"].setdefault(group, []).append({name: item["value"]})
                                continue
                            menu_dict["menuInfo"].setdefault(group, []).append({name: True})
                        else:
                            menu_dict["menuInfo"].setdefault(group, []).append({name: False})

    return response_ok(menu_dict)


def formatted_time(timestamp: int) -> str:
    """
    格式化时间戳
    """
    dt = datetime.datetime.fromtimestamp(timestamp / 1000)
    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
    return formatted_time
