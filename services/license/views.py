# coding=utf-8

import datetime
import hashlib
import os
import time

from sanic import Blueprint, response
from sanic.log import logger
from sanic.request import Request
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import get_license_sha256, load_license, requires_permission
from libs.utils import verify_license
from models import Account, PermissionsMenu, Playbook, Tool
from settings import LICENSE_DIR

license_bp = Blueprint("license", url_prefix="/license")
_all__ = ["license_bp"]


@license_bp.post("/verify")
@openapi.definition(
    summary="验证并上传 License 文件",
    description="验证 License 文件的有效性并上传，成功后会重启服务加载新配置",
    body={
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "License 文件 (.lic 格式)",
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
                            "code": {"type": "integer", "example": 0},
                            "data": {"type": "string", "example": "上传成功"},
                        },
                    }
                }
            },
        }
    },
)
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
@openapi.definition(
    summary="获取用户菜单权限",
    description="根据用户角色和 License 授权情况返回可访问的菜单列表",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["CN", "TW", "EN"],
                        "default": "CN",
                        "description": "语言选项：CN-简体中文, TW-繁体中文, EN-英文",
                    }
                },
            }
        }
    },
    response={
        "200": {
            "description": "菜单列表",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "array",
                                "description": "菜单项列表",
                            },
                        },
                    }
                }
            },
        }
    },
)
async def get_license(request: Request):
    """
    根据权限加载页面

    请求参数
    """
    language = request.json.get("language", "CN")
    permissions = await PermissionsMenu.all().values()
    lic = load_license()
    if not lic:
        return response_ok([p["url"][language] for p in permissions if p["model"] == "lic"])

    user = await Account.get(id=request.ctx.session["sub"]).prefetch_related("roles")
    if not user:
        raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)

    menu_dict = {}
    ai_type = 0
    tenant_type = False
    for role in user.roles:
        perm_models = set()
        if role.permissions:
            valid_modules = set()
            for item in lic["moduleList"]:
                name = item["name"]
                group = item["group"]
                if int(lic["grouplist"][group]["expireTime"]) > int(time.time() * 1000):
                    if item["module"] == "ai_bp":
                        ai_type += 1
                    if item["module"] == "tenant_bp":
                        tenant_type = True
                    valid_modules.add(name)
            valid_modules.update(["许可管理"])
            logger.info(f"valid_modules: {valid_modules}")
            # 超级权限处理
            if "*:*" not in role.permissions:
                relo_permissions_model = [p.split(":")[0] for p in role.permissions]
                permissions = await PermissionsMenu.filter(model__in=relo_permissions_model).all().values()
            perm_models.update([p["model"] for p in permissions if p["description"] in valid_modules and p["description"] != "仪表盘"])

        logger.info(f"perm_models: {perm_models}")
        for model in perm_models:
            permission_data = next((p for p in permissions if p["model"] == model), None)
            if not permission_data or not permission_data["url"]:
                continue

            key = permission_data["url"][language]["path"]
            if key not in menu_dict:
                menu_dict[key] = permission_data["url"][language]
            else:
                # 合并 children 去重
                existing = {c["path"] for c in menu_dict[key]["children"]}
                for child in permission_data["url"][language]["children"]:
                    if child["path"] not in existing:
                        menu_dict[key]["children"].append(child)
                        existing.add(child["path"])
            if model == "warroom":
                if ai_type == 0:
                    menu_dict[key]["ai"] = False
                else:
                    menu_dict[key]["ai"] = True

    # 菜单处理排序逻辑
    system_dict = {
        "产品许可": 0,
        "账号管理": 1,
        "角色管理": 2,
        "SSO集成": 3,
        "审计日志": 4,
        "工作空间": 5,
        "工作空間": 5,
        "Workspaces": 5,
        "个性化设置": 6,
        "系统状态": 7,
        "個性化設置": 6,
        "Personalization": 6,
        "審計日誌": 4,
        "Audit Logs": 4,
        "System Status": 7,
        "系統狀態": 7,
        "SSO Integration": 3,
        "Role Management": 2,
        "產品許可": 0,
        "License": 0,
        "賬號管理": 1,
        "Account Management": 1,
    }
    event_dict = {
        "事件列表": 0,
        "事件接入": 1,
        "事件清單": 0,
        "Event List": 0,
        "Event Ingestion": 1,
    }

    SPECIAL_TENANT_ID = "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18"
    current_tenant_id = request.ctx.session.get("tenant_id")

    hidden_menus = {
        "产品许可", "產品許可", "License",
        "SSO集成", "SSO Integration",
        "工作空间", "工作空間", "Workspaces",
        "个性化设置", "個性化設置", "Personalization",
        "安全设置", "安全設置", "Security Settings",
    }

    menus = list(menu_dict.values())
    for menu in menus:
        children = menu["children"]
        if menu["meta"]["title"] in ["系统设置", "系統設置", "System Settings"]:
            # 非特殊工作空间时，先把不允许的子菜单删掉再排序
            if current_tenant_id != SPECIAL_TENANT_ID:
                children = [
                    c for c in children if c.get("meta", {}).get("title") not in hidden_menus
                ]
            menu["children"] = sorted(children, key=lambda x: system_dict.get(x["meta"]["title"], 99))
        elif menu["meta"]["title"] in ["事件管理", "Event Management"]:
            menu["children"] = sorted(children, key=lambda x: event_dict.get(x["meta"]["title"], 99))

    if tenant_type:
        menus.append({"tenant": True})
    else:
        menus.append({"tenant": False})

    return response_ok(menus)


@license_bp.post("/show")
@openapi.definition(
    summary="查看 License 详细信息",
    description="获取当前 License 的详细信息，包括授权公司、版本、有效期、模块授权情况等",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {},
            }
        }
    },
    response={
        "200": {
            "description": "License 详细信息",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer", "example": 0},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "授权公司名称"},
                                    "version": {"type": "string", "description": "License 版本"},
                                    "createTime": {"type": "string", "description": "创建时间"},
                                    "expireTime": {"type": "string", "description": "过期时间"},
                                    "lic_status": {"type": "boolean", "description": "License 是否有效"},
                                    "serial_number": {"type": "string", "description": "序列号"},
                                    "menuInfo": {
                                        "type": "object",
                                        "description": "模块授权信息",
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
@requires_permission("lic:r")
async def get_all_license(request: Request):
    """
    获取所有license文件信息用于页面展示
    """
    consume_info = {
        "应用集成": await Tool.filter(deleted=False, maintain=False).count(),
        "剧本编排": await Playbook.filter(deleted=False, internal=False).count(),
    }

    menu_dict = dict()
    lic = load_license()

    if not lic:
        raise CustomError(ErrorCode.INVALID_LICENSE_ERROR)

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
        if key in ["剧本编排", "应用集成"]:
            if int(item.get("value", 0)) == 99999:
                val = "无限"
            else:
                val = str(consume_info.get(key, 0)) + "/" + str(item.get("value", 0))
        menu_entry = menu_dict["menuInfo"].setdefault(key, {})
        menu_entry["value"] = val
        menu_entry["auth"] = True if int(item.get("expireTime")) > int(time.time() * 1000) else False
        menu_entry["description"] = item.get("description")
        menu_entry["expireTime"] = fmt_time(int(item.get("expireTime")))

    menu_dict["serial_number"] = get_license_sha256()

    return response_ok(menu_dict)


def fmt_time(timestamp: int) -> str:
    """
    格式化时间戳
    """
    dt = datetime.datetime.fromtimestamp(int(timestamp) / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
