# coding=utf-8

import datetime
import hashlib
import os
import time

from sanic import Blueprint
from sanic import response
from sanic.request import Request

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission, load_license
from libs.utils import verify_license
from models import Tool, Playbook
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
    request.app.m.restart(zero_downtime=True, all_workers=True)
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
    consume_info = {
        "应用数量": await Tool.filter(deleted=False).count(),
        "剧本数量限制": await Playbook.filter(deleted=False).count()
    }

    advanced_modules = ["虚拟作战室", "AI辅助", "集群部署", "软件升级", "维保服务", "多引擎部署"]
    advanced_groups = ["高级功能"]

    menu_dict = dict()
    lic = load_license()

    if not lic:
        raise CustomError(ErrorCode.InvalidLicenseError)

    # 基础字段赋值
    menu_dict["name"] = lic["company"]
    menu_dict["version"] = lic["version"]
    menu_dict["createTime"] = fmt_time(lic.get("createTime"))
    menu_dict["expireTime"] = fmt_time(lic.get("expireTime"))
    menu_dict["lic_status"] = True if lic.get("expireTime") > int(time.time() * 1000) else False
    menu_dict["menuInfo"] = {}

    # 获取菜单信息
    menu_info = lic.get("menuInfo", {})
    module_list = menu_info.get("moduleList", [])
    group_list = menu_info.get("grouplist", {})

    for item in module_list:
        name = item.get("name")
        group = item.get("group")
        auth = item.get("auth")
        expire_time = item.get("expireTime")
        value = item.get("value")

        # 时间有效性判断
        try:
            expire_time_int = int(expire_time)
            current_time_int = int(time.time() * 1000)
            is_valid = auth and expire_time_int > current_time_int
        except (TypeError, ValueError):
            is_valid = False

        if name in advanced_modules or group in advanced_groups:
            key = name
            val = None
        else:
            key = group
            val = str(consume_info[name]) + "/" + str(value) if name in ["应用数量", "剧本数量限制"] else None

        description = group_list.get(group, "") if group != "高级功能" else group_list.get(name, "")

        menu_entry = menu_dict["menuInfo"].setdefault(key, {})
        menu_entry["value"] = val
        menu_entry["auth"] = True if is_valid else True
        menu_entry["description"] = description
        menu_entry["expireTime"] = fmt_time(expire_time)

    return response_ok(menu_dict)


def fmt_time(timestamp: int) -> str:
    """
    格式化时间戳
    """
    dt = datetime.datetime.fromtimestamp(int(timestamp) / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
