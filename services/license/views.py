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

license_bp = Blueprint('license', version=1, url_prefix='/license')
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
    "warroom_bp": "warroom",
    "dashboard_bp": "dashboard"
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
    file_content = license_file.body
    verify_license(file_content)
    new_filename = hashlib.md5(file_content).hexdigest() + ".lic"
    new_file_path = os.path.join(LICENSE_DIR, new_filename)

    for filename in os.listdir(LICENSE_DIR):
        if filename.endswith(".lic"):
            old_file_path = os.path.join(LICENSE_DIR, filename)
            if os.path.isfile(old_file_path):
                os.remove(old_file_path)

    with open(new_file_path, "wb") as f:
        f.write(file_content)

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
    for item in lic["moduleList"]:
        module = item["module"]
        auth = item["auth"]

        if module not in menu_dict:
            menu_dict[module] = False
        if auth and int(lic["grouplist"][item["group"]]["expireTime"]) > int(time.time() * 1000):
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
        "应用集成": await Tool.filter(deleted=False).count(),
        "剧本编排": await Playbook.filter(deleted=False).count()
    }

    menu_dict = dict()
    lic = load_license()

    if not lic:
        raise CustomError(ErrorCode.InvalidLicenseError)

    # 基础字段赋值
    menu_dict["name"] = lic["company"]
    menu_dict["version"] = lic["version"]
    menu_dict["createTime"] = fmt_time(lic.get("createTime"))
    menu_dict["expireTime"] = fmt_time(lic.get("expireTime"))
    menu_dict["lic_status"] = True if int(lic.get("expireTime")) > int(time.time() * 1000) else False
    menu_dict["menuInfo"] = {}

    # 获取菜单信息
    group_list = lic.get("grouplist", {})

    for key, item in group_list.items():
        val = None
        if key in ["剧本编排","应用集成"]:
            if int(item.get("value", 0)) == 99999:
                val = "无限"
            else:
                val = str(consume_info.get(key, 0)) + "/" + str(item.get("value", 0))
        menu_entry = menu_dict["menuInfo"].setdefault(key, {})
        menu_entry["value"] = val
        menu_entry["auth"] = True if int(item.get("expireTime")) > int(time.time() * 1000) else False
        menu_entry["description"] = item.get("description")
        menu_entry["expireTime"] = fmt_time(int(item.get("expireTime")))

    return response_ok(menu_dict)


def fmt_time(timestamp: int) -> str:
    """
    格式化时间戳
    """
    dt = datetime.datetime.fromtimestamp(int(timestamp) / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
