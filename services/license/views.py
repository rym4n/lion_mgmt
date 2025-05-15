# coding=utf-8

import datetime
import hashlib
import os
import threading
import time

from sanic import Blueprint
from sanic import response
from sanic.request import Request

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission, load_license
from libs.utils import verify_license
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
    verify_license(license_file.body)
    license_path = hashlib.md5(license_file.body).hexdigest() + ".lic"
    for root, dirs, files in os.walk(LICENSE_DIR):
        for file in files:
            full_path = os.path.join(root, file)
            new_path = os.path.join(root, license_path)
            with open(full_path, 'wb') as f:
                f.write(license_file.body)
            if os.path.exists(full_path):
                os.rename(full_path, new_path)
    threading.Thread(target=delayed_exit, daemon=True).start()
    return response_ok("上传成功，正在加载配置文件，请稍后！")


@license_bp.post("/get")
@requires_permission("lic:r")
async def get_license(request: Request):
    """
    获取许可文件权限，加载页面

    无需请求参数
    """
    menu_dict = {}
    lic = load_license()
    if not lic:
        raise CustomError(ErrorCode.InvalidLicenseError)
    for item in lic["menuInfo"]["moduleList"]:
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
    menu_dict = dict()
    lic = load_license()
    if not lic:
        raise CustomError(ErrorCode.InvalidLicenseError)
    menu_dict["name"] = lic["company"]
    menu_dict["version"] = lic["version"]
    menu_dict["createTime"] = formatted_time(lic["createTime"])
    menu_dict["expireTime"] = formatted_time(lic["expireTime"])
    menu_dict["menuInfo"] = {}
    for item in lic["menuInfo"]["moduleList"]:
        name = item["name"]
        auth = item["auth"]
        group = item["group"]
        description = item["description"]

        if auth and int(item["expireTime"]) > int(time.time() * 1000):
            if name in ["虚拟作战室", "AI辅助", "集群部署", "软件升级", "维保服务", "多引擎部署"] or group in ["高级功能"]:
                menu_dict["menuInfo"].setdefault(name, {})["value"] = None
                menu_dict["menuInfo"].setdefault(name, {})["auth"] = True
                menu_dict["menuInfo"].setdefault(name, {})["description"] = description
                menu_dict["menuInfo"].setdefault(name, {})["expireTime"] = formatted_time(item["expireTime"])
                continue
            menu_dict["menuInfo"].setdefault(group, {})["value"] = item["value"] if name in ["应用数量", "剧本数量限制"] else None
            menu_dict["menuInfo"].setdefault(group, {})["auth"] = True
            menu_dict["menuInfo"].setdefault(group, {})["description"] = lic["menuInfo"]["grouplist"].get(group, "")
            menu_dict["menuInfo"].setdefault(group, {})["expireTime"] = formatted_time(item["expireTime"])
        else:
            if name in ["虚拟作战室", "AI辅助", "集群部署", "软件升级", "维保服务", "多引擎部署"] or group in ["高级功能"]:
                menu_dict["menuInfo"].setdefault(name, {})["value"] = None
                menu_dict["menuInfo"].setdefault(name, {})["auth"] = True
                menu_dict["menuInfo"].setdefault(name, {})["description"] = description
                menu_dict["menuInfo"].setdefault(name, {})["expireTime"] = formatted_time(item["expireTime"])
                continue
            menu_dict["menuInfo"].setdefault(group, {})["value"] = None
            menu_dict["menuInfo"].setdefault(group, {})["auth"] = True
            menu_dict["menuInfo"].setdefault(group, {})["description"] = lic["menuInfo"]["grouplist"].get(group, "")
            menu_dict["menuInfo"].setdefault(group, {})["expireTime"] = formatted_time(item["expireTime"])

    return response_ok(menu_dict)


def formatted_time(timestamp: int) -> str:
    """
    格式化时间戳
    """
    dt = datetime.datetime.fromtimestamp(int(timestamp) / 1000)
    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
    return formatted_time
