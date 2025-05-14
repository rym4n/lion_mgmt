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

DESCRIPTION_DICT = {
    "用户列表": "支持创建本地账号、LDAP账号，支持账号有效期配置、双因素认证绑定",
    "角色管理": "预定义系统管理员、审计员、事件处理员、第三方协调员等角色，同时支持自定义角色，权限的颗粒度细化到每项子功能的查询、添加、修改、删除等",
    "权限管理": "支持基于角色定义用户权限，用户绑定一个或多个角色",
    "系统监控": "支持系统模块、服务实时监控，支持系统日志导出",
    "系统审计": "系统审计日志包含时间、用户、IP地址、操作、类型、结果以及操作原始详情",
    "系统备份与恢复": "支持定时、即时系统备份操作，支持系统配置恢复",
    "事件接入": "支持kafka消息、API、syslog、邮件等多种事件接入方式",
    "事件自动去重": "支持自定义去重规则，事件接入自动合并重复事件",
    "事件分类": "支持自定义事件类型，根据事件类型关联处置动作或处置剧本",
    "事件处理详情": "展示事件处置的详细内容，包括事件生命周期各阶段、剧本、动作详细输入、输出信息，去重事件关联关系",
    "事件报告": "每条事件处理完成后，自动生成事件处理报告",
    "应用APP开发SDK": "提供Python APP开发SDK，提供APP开发框架自动生成工具等快速APP开发工具包",
    "应用集成对接方式": "支持API、SSH/Telnet、webSocket、HTTP、JDBC、SOAP、SNMP以及内部私有协议等多种对接方式",
    "开放API接口": "开放南、北向API接口，包括事件接入API接口、报表查看API接口、应用对接API接口等",
    "应用APP支持": "免费提供厂家已开发的APP下载，已经支持主流的防火墙、WAF、IPS、扫描器、威胁情报、网络设备、蜜罐等类型安全系统",
    "应用权限控制": "支持支持细粒度权限控制，针对每资源、每动作配置权限，仅允许已配置的用户、角色执行动作.",
    "应用数量": "除系统内置应用外，限制上传使用的应用数量",
    "剧本编排": "剧本编排支持普通动作、规则引擎、审批节点、虚拟节点、异步节点等多种节点类型；支持草稿模式、安全模式、生产模式；支持剧本克隆；支持导入、导出功能；支持绑定安全场景、定义标签等",
    "剧本数量限制": "除系统内置剧本外，限制新建的剧本数量",
    "安全场景管理": "根据安全保障级别自定义安全场景，每个安全场景关联一组安全剧本，支持一键切换安全场景",
    "仪表盘": "支持安全态势、事件趋势、事件处理时效、系统性能状况等仪表板展示",
    "虚拟作战室": "默认支持10个使用授权。作战室支持全局模式、单事件模式；支持多人协同交流；支持AI机器人交互；支持任意动作、剧本执行；支持命令行快捷执行；支持专注模式，消除交互过程的噪音",
    "剧本编排高级功能": "剧本编排支持循环、嵌套、函数等高级功能",
    "快速编排功能": "支持从事件处理详情、作战室快速生成剧本",
    "事件生命周期管理": "依据事件生命周期：调查取证、分析、遏制、根除、恢复、复盘各阶段进行管理，每阶段自动执行关联任务，完成事件处置工作",
    "集群部署": "系统支持多台集群，平滑扩展",
    "多引擎部署": "系统支持多执行引擎分布式部署，依据网络架构、性能需求、物理位置、云架构等情况，灵活部署多个执行引擎",
    "人工审核": "人工审核",
    "全局变量": "全局变量",
    "许可管理": "展示系统版本信息、授权模块信息、授权过期时间等",
    "场景管理": "场景管理",
    "定时任务": "定时任务"
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

        if auth and int(item["expireTime"]) > int(time.time() * 1000):
            if name in ["应用数量", "剧本数量限制"]:
                menu_dict["menuInfo"].setdefault(group, []).append({"name": name, "value": item["value"], "auth": True, "description": DESCRIPTION_DICT[name]})
                continue
            menu_dict["menuInfo"].setdefault(group, []).append({"name": name, "value": None, "auth": True, "description": DESCRIPTION_DICT[name]})
        else:
            menu_dict["menuInfo"].setdefault(group, []).append({"name": name, "value": None, "auth": False, "description": DESCRIPTION_DICT[name]})

    return response_ok(menu_dict)


def formatted_time(timestamp: int) -> str:
    """
    格式化时间戳
    """
    dt = datetime.datetime.fromtimestamp(timestamp / 1000)
    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
    return formatted_time
