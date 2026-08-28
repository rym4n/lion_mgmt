# coding=utf-8

import datetime
import hashlib
import os
import tempfile
import time

from sanic import Blueprint, response
from sanic.log import logger
from sanic.request import Request
from sanic_ext import openapi

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import check_wildcard, get_license_sha256, load_license, requires_permission
from libs.security.license_modules import is_licensed_module_active
from libs.utils import verify_license
from models import Account, PermissionsMenu, Playbook, Tool
from settings import LICENSE_DIR

license_bp = Blueprint("license", url_prefix="/license")
_all__ = ["license_bp"]


def _permission_values(permissions: object) -> list[str]:
    if not isinstance(permissions, list):
        return []

    values = []
    for item in permissions:
        if isinstance(item, dict):
            value = item.get("value")
        else:
            value = item
        if isinstance(value, str) and value:
            values.append(value)
    return values


def _role_can_open_menu(role_permissions: list[str] | None, menu: dict) -> bool:
    if not role_permissions:
        return False
    if "*:*" in role_permissions:
        return True

    menu_model = menu.get("model")
    menu_permission_values = [
        value if ":" in value else f"{menu_model}:{value}"
        for value in _permission_values(menu.get("Permissions"))
        if menu_model or ":" in value
    ]
    return any(
        check_wildcard(role_permission, menu_permission)
        for role_permission in role_permissions
        for menu_permission in menu_permission_values
    )


def _licensed_module_keys(license_data: dict) -> set[str]:
    valid_modules = {"许可管理", "license_bp"}
    for item in license_data.get("moduleList", []):
        if not is_licensed_module_active(license_data, item):
            continue
        name = item.get("name")
        module = item.get("module")
        if name:
            valid_modules.add(name)
            if name == "工作空间":
                valid_modules.add("租户管理")
        if module:
            valid_modules.add(module)
    return valid_modules


def _menu_is_licensed(menu: dict, valid_modules: set[str]) -> bool:
    return menu.get("description") in valid_modules or menu.get("bp") in valid_modules


def _worker_license_data(request: Request):
    app_ctx = getattr(getattr(request, "app", None), "ctx", None)
    if app_ctx is not None and hasattr(app_ctx, "license_data"):
        return app_ctx.license_data
    return load_license()


def _worker_license_sha256(request: Request):
    app_ctx = getattr(getattr(request, "app", None), "ctx", None)
    if app_ctx is not None and hasattr(app_ctx, "active_license_sha256"):
        return app_ctx.active_license_sha256
    return get_license_sha256()


@license_bp.post("/machine-code")
@openapi.definition(
    summary="获取当前机器码",
    description="返回当前服务器的机器指纹码，用于申请 License",
)
@requires_permission("lic:r")
async def get_machine_code(request: Request):
    from libs.utils import generate_fingerprint

    try:
        machine_code = generate_fingerprint()
        return response_ok({"machine_code": machine_code})
    except CustomError:
        raise
    except Exception as e:
        logger.error(f"获取机器码失败: {e}")
        raise CustomError(ErrorCode.ANCHOR_NOT_FOUND)


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
                            "data": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                    "license_sha256": {"type": "string"},
                                    "required_modules": {
                                        "type": "array",
                                        "items": {"type": "string"},
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
    license_data = verify_license(file_content)
    new_filename = hashlib.md5(file_content).hexdigest() + ".lic"
    new_file_path = os.path.join(LICENSE_DIR, new_filename)

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=LICENSE_DIR,
            prefix=f".{new_filename}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            temporary_file.write(file_content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, new_file_path)
        temporary_path = None
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)

    for filename in os.listdir(LICENSE_DIR):
        if filename == new_filename or not filename.endswith(".lic"):
            continue
        old_file_path = os.path.join(LICENSE_DIR, filename)
        if os.path.isfile(old_file_path):
            os.remove(old_file_path)

    license_sha256 = hashlib.sha256(file_content).hexdigest()
    supported_modules = set(
        getattr(request.app.ctx, "supported_license_modules", ())
    )
    required_modules = sorted(
        {
            item.get("module")
            for item in license_data.get("moduleList", [])
            if item.get("module") in supported_modules
            and is_licensed_module_active(license_data, item)
        }
    )

    request.app.m.restart(zero_downtime=True, all_workers=True)

    return response_ok(
        {
            "message": "上传成功，正在加载配置文件，请稍后！",
            "license_sha256": license_sha256,
            "required_modules": required_modules,
        }
    )


@license_bp.post("/reload-status")
@openapi.definition(
    summary="获取 License 重载状态",
    description="返回当前工作进程加载的 License 和已注册模块，用于等待无损重载完成",
)
@requires_permission("lic:c", "lic:r")
async def get_license_reload_status(request: Request):
    app_ctx = request.app.ctx
    return response_ok(
        {
            "ready": bool(getattr(app_ctx, "worker_ready", False)),
            "license_sha256": getattr(app_ctx, "active_license_sha256", None),
            "registered_modules": sorted(
                getattr(app_ctx, "registered_license_modules", ())
            ),
        }
    )


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
    current_tenant_id = request.ctx.session.get("tenant_id")
    if not current_tenant_id:
        raise CustomError(ErrorCode.SESSION_INVALID)

    permissions = await PermissionsMenu.filter(deleted=False).values()
    lic = _worker_license_data(request)
    if not lic:
        return response_ok([p["url"][language] for p in permissions if p["model"] == "lic"])

    user = await Account.get(id=request.ctx.session["sub"]).prefetch_related("roles")
    if not user:
        raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)

    menu_dict = {}
    valid_modules = _licensed_module_keys(lic)
    active_modules = {
        item.get("module")
        for item in lic.get("moduleList", [])
        if is_licensed_module_active(lic, item)
    }
    ai_type = int("ai_bp" in active_modules)
    tenant_type = "tenant_bp" in active_modules
    for role in user._roles_for_tenant(current_tenant_id):
        perm_models = set()
        if role.permissions:
            # 超级权限处理
            if "*:*" not in role.permissions:
                role_permissions = [
                    permission
                    for permission in permissions
                    if _role_can_open_menu(role.permissions, permission)
                ]
            else:
                role_permissions = permissions
            perm_models.update(
                [
                    p["model"]
                    for p in role_permissions
                    if _menu_is_licensed(p, valid_modules) and p["description"] != "仪表盘"
                ]
            )

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
        "租户管理": 5,
        "租戶管理": 5,
        "Tenant": 5,
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
    # 专家团队的 children 由多条 permissions_menu 记录合并而来，而合并顺序取决于
    # perm_models 这个 set 的遍历顺序，本身不稳定。显式排序保证菜单次序固定。
    expert_team_dict = {
        "指挥中心": 0,
        "指揮中心": 0,
        "Command Center": 0,
        "数字员工": 1,
        "數字員工": 1,
        "Digital Employees": 1,
        "技能管理": 2,
        "Skill Management": 2,
        "汇报助手": 3,
        "匯報助手": 3,
        "Report Assistant": 3,
        "知识空间": 4,
        "知識空間": 4,
        "Knowledge Space": 4,
    }
    top_menu_order = {
        "作战室": 10,
        "作戰室": 10,
        "War Room": 10,
        "指挥中心": 15,
        "指揮中心": 15,
        "Command Center": 15,
        "资产管理": 16,
        "資產管理": 16,
        "Asset Management": 16,
        "网络管理": 17,
        "網絡管理": 17,
        "Network Management": 17,
        "事件管理": 20,
        "Event Management": 20,
    }

    SPECIAL_TENANT_ID = "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18"
    hidden_menus = {
        "产品许可",
        "產品許可",
        "License",
        "SSO集成",
        "SSO Integration",
        "租户管理",
        "租戶管理",
        "Tenant",
        "个性化设置",
        "個性化設置",
        "Personalization",
        "安全设置",
        "安全設置",
        "Security Settings",
        "模型配置",
        "Model Config",
    }

    menus = list(menu_dict.values())
    for menu in menus:
        children = menu["children"]
        if menu["meta"]["title"] in ["系统设置", "系統設置", "System Settings"]:
            # 非特殊租户时，先把不允许的子菜单删掉再排序
            if current_tenant_id != SPECIAL_TENANT_ID:
                children = [c for c in children if c.get("meta", {}).get("title") not in hidden_menus]
            menu["children"] = sorted(children, key=lambda x: system_dict.get(x["meta"]["title"], 99))
        elif menu["meta"]["title"] in ["事件管理", "Event Management"]:
            menu["children"] = sorted(children, key=lambda x: event_dict.get(x["meta"]["title"], 99))
        elif menu["meta"]["title"] in ["专家团队", "專家團隊", "Expert Team"]:
            menu["children"] = sorted(
                children, key=lambda x: expert_team_dict.get(x["meta"]["title"], 99)
            )
        # Split licensed menus reuse one parent route. A historical parent
        # redirect may point to a sibling that is not in this response.
        if menu.get("children"):
            child_paths = {child.get("path") for child in menu["children"]}
            if menu.get("redirect") not in child_paths:
                menu["redirect"] = menu["children"][0].get("path")
        title = menu.get("meta", {}).get("title")
        if title in top_menu_order:
            menu["meta"]["rank"] = top_menu_order[title]

    menus = sorted(
        menus,
        key=lambda menu: (
            menu.get("meta", {}).get("rank", 99),
            menu.get("meta", {}).get("title", ""),
        ),
    )

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
    # 按工具名称去重统计应用数量（同一工具的多个版本只算一个应用）
    tool_names = await Tool.filter(deleted=False, maintain=False).values_list("name", flat=True)
    consume_info = {
        "应用集成": len(set(tool_names)),
        "剧本编排": await Playbook.filter(deleted=False, internal=False, playbook_versions__deleted=False, playbook_versions__status=1).distinct().count()
    }

    menu_dict = dict()
    lic = _worker_license_data(request)

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

    menu_dict["serial_number"] = _worker_license_sha256(request)

    return response_ok(menu_dict)


def fmt_time(timestamp: int) -> str:
    """
    格式化时间戳
    """
    dt = datetime.datetime.fromtimestamp(int(timestamp) / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
