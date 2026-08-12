# coding=utf-8
from sanic import Blueprint
from sanic_ext import openapi
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import (
    PLAYBOOK_MODULE_TO_FINE_PERMISSIONS,
    PLAYBOOK_UNCONTROLLED_PERMISSION,
    SUPERADMIN_PERMISSION,
    TOOL_RESOURCE_UNCONTROLLED_PERMISSION,
    check_wildcard,
    is_superadmin_role,
    load_license,
    requires_permission,
    workflow_uncontrolled_actions,
)
from libs.security.license_modules import is_licensed_module_active
from models import Account, PermissionsMenu, Role

role_bp = Blueprint("role", url_prefix="/role")
_all__ = ["role_bp"]


LEGACY_PERMISSION_REMAP = {
    "agent:report:schedule:c": "agent_report_schedule:c",
    "agent:report:schedule:r": "agent_report_schedule:r",
    "agent:report:schedule:u": "agent_report_schedule:u",
    "agent:report:schedule:d": "agent_report_schedule:d",
    "digital_employee:role:c": "digital_employee:c",
    "digital_employee:role:r": "digital_employee:r",
    "digital_employee:role:u": "digital_employee:u",
    "digital_employee:role:d": "digital_employee:d",
    "digital_employee:skill:c": "digital_employee:c",
    "digital_employee:skill:r": "digital_employee:r",
    "digital_employee:skill:u": "digital_employee:u",
    "digital_employee:skill:d": "digital_employee:d",
    "digital_employee:report:r": "digital_employee:r",
    "digital_employee:report:u": "digital_employee:u",
    "digital_employee:package:import": "digital_employee:c",
    "digital_employee:package:export": "digital_employee:r",
    "digital_employee:package:r": "digital_employee:r",
    "digital_employee:package:d": "digital_employee:d",
}

ACTION_LABEL_PREFIXES = {
    "c": "新增",
    "r": "查看",
    "u": "修改",
    "d": "删除",
    "w": "编辑",
}

PERMISSION_RESOURCE_LABELS = {
    "agent_report_schedule": "汇报任务",
    "command_center": "作战室",
    "digital_employee": "数字员工",
    "digital_employee_skill": "技能管理",
    "network_topology": "网络拓扑",
    "asset_management": "资产管理",
    "network_access_control": "网络访问控制",
}

TOOL_VIEW_PERMISSION = "tool:r"
TOOL_LEGACY_ACTION_TO_FINE_PERMISSIONS = {
    "c": {"tool:upload:c", "tool:resource:c"},
    "r": {"tool:r", "tool:health:r", "tool:debug:r"},
    "u": {"tool:health:r", "tool:resource:u", "tool:engine:u"},
    "d": {"tool:app:d", "tool:resource:d"},
}

MENU_LANGUAGE = "CN"

SYSTEM_MENU_CHILD_ORDER = {
    "产品许可": 0,
    "產品許可": 0,
    "License": 0,
    "账号管理": 1,
    "賬號管理": 1,
    "Account Management": 1,
    "角色管理": 2,
    "Role Management": 2,
    "SSO集成": 3,
    "SSO Integration": 3,
    "审计日志": 4,
    "審計日誌": 4,
    "Audit Logs": 4,
    "租户管理": 5,
    "租戶管理": 5,
    "Tenant": 5,
    "个性化设置": 6,
    "個性化設置": 6,
    "Personalization": 6,
    "系统状态": 7,
    "系統狀態": 7,
    "System Status": 7,
    "安全设置": 8,
    "安全設置": 8,
    "Security Settings": 8,
}

EVENT_MENU_CHILD_ORDER = {
    "事件列表": 0,
    "事件清單": 0,
    "Event List": 0,
    "事件接入": 1,
    "Event Ingestion": 1,
}

TOP_MENU_ORDER = {
    "仪表盘": 0,
    "儀表盤": 0,
    "Dashboard": 0,
    "作战室": 10,
    "作戰室": 10,
    "War Room": 10,
    "专家团队": 15,
    "專家團隊": 15,
    "Expert Team": 15,
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

FIXED_CHILD_ROUTE_ORDER = {
    "/command-center": [
        "/command-center/board",
        "/command-center/employees",
        "/command-center/skills",
        "/command-center/report-assistant",
    ],
    "/asset-management": [
        "/asset-management/models",
        "/asset-management/catalog",
    ],
    "/network-management": [
        "/network-management/topology",
        "/network-management/access-control",
    ],
}

MODEL_MENU_FALLBACK_ORDER = {
    "dashboard": (0, 0),
    "warroom": (10, 0),
    "command_center": (15, 0),
    "digital_employee": (15, 1),
    "digital_employee_skill": (15, 2),
    "agent": (15, 3),
    "agent_report_schedule": (15, 4),
}

PERMISSION_MENU_LICENSE_ALIASES = {
    "tool": {"自定义应用", "应用管理", "应用市场"},
}


def _preserve_superadmin_permission(role, permissions: list[str]) -> list[str]:
    if permissions and is_superadmin_role(role) and SUPERADMIN_PERMISSION not in permissions:
        return [*permissions, SUPERADMIN_PERMISSION]
    return permissions


def _playbook_permission_control(role_permissions: list[str] | None) -> str:
    controls = _playbook_permission_controls(role_permissions)
    if any(control == "uncontrolled" for control in controls.values()):
        return "uncontrolled"
    return "controlled"


def _playbook_permission_controls(role_permissions: list[str] | None) -> dict[str, str]:
    controls = {
        action: "controlled"
        for action in PLAYBOOK_MODULE_TO_FINE_PERMISSIONS
    }
    for permission_value in role_permissions or []:
        normalized = _normalize_permission_value(permission_value)
        if normalized == SUPERADMIN_PERMISSION:
            return {action: "uncontrolled" for action in controls}
        if normalized == PLAYBOOK_UNCONTROLLED_PERMISSION:
            return {action: "uncontrolled" for action in controls}
        for action in workflow_uncontrolled_actions(normalized):
            controls[action] = "uncontrolled"
    return controls


def _tool_resource_permission_control(role_permissions: list[str] | None) -> str:
    for permission_value in role_permissions or []:
        normalized = _normalize_permission_value(permission_value)
        if normalized == SUPERADMIN_PERMISSION:
            return "uncontrolled"
        if normalized == TOOL_RESOURCE_UNCONTROLLED_PERMISSION:
            return "uncontrolled"
    return "controlled"


def _normalize_permission_value(permission_value: object) -> str | None:
    if not isinstance(permission_value, str) or ":" not in permission_value:
        return None
    if permission_value.startswith("agent:report:schedule:"):
        return f"agent_report_schedule:{permission_value.removeprefix('agent:report:schedule:')}"
    return LEGACY_PERMISSION_REMAP.get(permission_value, permission_value)


def _needs_tool_view_permission(permission_value: object) -> bool:
    normalized = _normalize_permission_value(permission_value)
    return bool(
        normalized
        and normalized.startswith("tool:")
        and normalized != TOOL_VIEW_PERMISSION
        and normalized.count(":") >= 2
    )


def _with_implicit_tool_view_permission(permissions: list[str]) -> list[str]:
    if not permissions or TOOL_VIEW_PERMISSION in permissions:
        return permissions
    if any(_needs_tool_view_permission(permission) for permission in permissions):
        return [*permissions, TOOL_VIEW_PERMISSION]
    return permissions


def _legacy_tool_permission_matches(
    role_permission: str,
    actual_permission: str,
) -> bool:
    if (
        not actual_permission.startswith("tool:")
        or actual_permission == TOOL_VIEW_PERMISSION
    ):
        return False
    normalized = _normalize_permission_value(role_permission)
    if not normalized or not normalized.startswith("tool:"):
        return False
    legacy_actions = normalized.split(":", 1)[1].split(",")
    if "*" in legacy_actions:
        return True
    return any(
        actual_permission in TOOL_LEGACY_ACTION_TO_FINE_PERMISSIONS.get(action, set())
        for action in legacy_actions
    )


def _role_permission_matches(role_permission: str, actual_permission: str) -> bool:
    return check_wildcard(
        role_permission,
        actual_permission,
    ) or _legacy_tool_permission_matches(
        role_permission,
        actual_permission,
    )


def _normalize_menu_action_value(permission_value: object) -> str | None:
    if not isinstance(permission_value, str) or not permission_value:
        return None
    return permission_value.rsplit(":", 1)[-1]


def _permission_resource_key(permission_value: object) -> str | None:
    normalized = _normalize_permission_value(permission_value)
    if not normalized or ":" not in normalized:
        return None
    return normalized.rsplit(":", 1)[0]


def _normalize_action_label(
    label: object,
    action_value: str,
    menu_description: str | None,
    raw_value: object,
) -> str:
    label_value = label if isinstance(label, str) else ""
    if (
        label_value
        and label_value != action_value
        and label_value != raw_value
        and ":" not in label_value
    ):
        return label_value

    prefix = ACTION_LABEL_PREFIXES.get(action_value)
    resource_label = PERMISSION_RESOURCE_LABELS.get(_permission_resource_key(raw_value)) or menu_description
    if prefix and resource_label:
        return f"{prefix}{resource_label}权限"
    return label_value or action_value


def _normalize_menu_permissions(
    permissions: object,
    menu_model: str | None = None,
    menu_description: str | None = None,
) -> list[dict]:
    if not isinstance(permissions, list):
        return []

    normalized_permissions = []
    seen_permissions = set()
    for item in permissions:
        if isinstance(item, dict):
            raw_value = item.get("value")
        else:
            raw_value = item
        value = _normalize_menu_action_value(raw_value)
        if not value:
            continue

        label = item.get("label") if isinstance(item, dict) else None
        actual_permission = _normalize_permission_value(raw_value)
        dedupe_key = actual_permission or (f"{menu_model}:{value}" if menu_model else value)
        if dedupe_key in seen_permissions:
            continue
        seen_permissions.add(dedupe_key)

        normalized_item = {
            "value": value,
            "label": _normalize_action_label(label, value, menu_description, raw_value),
        }
        default_permission = f"{menu_model}:{value}" if menu_model else None
        if actual_permission and default_permission and actual_permission != default_permission:
            normalized_item["permission"] = actual_permission
        normalized_permissions.append(normalized_item)

    return normalized_permissions


def _permission_menu_route(permission: dict) -> dict | None:
    url = permission.get("url")
    if not isinstance(url, dict) or not url:
        return None
    route = url.get(MENU_LANGUAGE)
    if isinstance(route, dict):
        return route
    return next((item for item in url.values() if isinstance(item, dict)), None)


def _menu_meta(node: dict | None) -> dict:
    if not isinstance(node, dict):
        return {}
    meta = node.get("meta")
    return meta if isinstance(meta, dict) else {}


def _menu_title(node: dict | None) -> str | None:
    title = _menu_meta(node).get("title")
    return title if isinstance(title, str) and title else None


def _menu_rank(node: dict | None, default: int = 99) -> int:
    rank = _menu_meta(node).get("rank")
    if isinstance(rank, bool):
        return default
    if isinstance(rank, int):
        return rank
    if isinstance(rank, float):
        return int(rank)
    if isinstance(rank, str) and rank.isdigit():
        return int(rank)
    return default


def _permission_menu_child(route: dict | None) -> dict | None:
    children = route.get("children") if isinstance(route, dict) else None
    if not isinstance(children, list):
        return None
    return next((child for child in children if isinstance(child, dict)), None)


def _permission_menu_display_description(permission: dict) -> str | None:
    route = _permission_menu_route(permission)
    child = _permission_menu_child(route)
    description = permission.get("description")
    return (
        _menu_title(child)
        or _menu_title(route)
        or (description if isinstance(description, str) else None)
    )


def _permission_menu_child_order(route: dict | None, child: dict | None) -> int:
    route_path = route.get("path") if isinstance(route, dict) else None
    child_path = child.get("path") if isinstance(child, dict) else None
    if isinstance(route_path, str) and isinstance(child_path, str):
        fixed_order = FIXED_CHILD_ROUTE_ORDER.get(route_path)
        if fixed_order and child_path in fixed_order:
            return fixed_order.index(child_path)

    child_title = _menu_title(child)
    route_title = _menu_title(route)
    if route_title in {"系统设置", "系統設置", "System Settings"}:
        return SYSTEM_MENU_CHILD_ORDER.get(child_title, 99)
    if route_title in {"事件管理", "Event Management"}:
        return EVENT_MENU_CHILD_ORDER.get(child_title, 99)
    return _menu_rank(child, 0)


def _permission_menu_sort_key(permission: dict, index: int = 0) -> tuple:
    model = permission.get("model")
    route = _permission_menu_route(permission)
    child = _permission_menu_child(route)
    title = _permission_menu_display_description(permission) or ""

    if not route:
        top_rank, child_order = MODEL_MENU_FALLBACK_ORDER.get(model, (99, 99))
        return top_rank, child_order, title, model or "", index

    route_title = _menu_title(route)
    top_rank = TOP_MENU_ORDER.get(route_title, _menu_rank(route))
    child_order = _permission_menu_child_order(route, child)
    child_path = child.get("path", "") if isinstance(child, dict) else ""
    return top_rank, child_order, title, child_path, model or "", index


def _ordered_permission_menus(permissions: list[dict]) -> list[dict]:
    indexed_permissions = [
        (index, permission)
        for index, permission in enumerate(permissions)
        if isinstance(permission, dict)
    ]
    return [
        permission
        for index, permission in sorted(
            indexed_permissions,
            key=lambda item: _permission_menu_sort_key(item[1], item[0]),
        )
    ]


def _normalize_permission_menu(permission: dict) -> dict:
    normalized = dict(permission)
    menu_description = _permission_menu_display_description(permission) or ""
    normalized["description"] = menu_description
    normalized["Permissions"] = _normalize_menu_permissions(
        permission.get("Permissions"),
        permission.get("model"),
        menu_description,
    )
    return normalized


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


def _permission_menu_is_licensed(permission: dict, valid_modules: set[str]) -> bool:
    module_names = [
        permission.get("description"),
        permission.get("bp"),
        _permission_menu_display_description(permission),
    ]
    model = permission.get("model")
    if isinstance(model, str):
        module_names.extend(PERMISSION_MENU_LICENSE_ALIASES.get(model, set()))
    return any(name in valid_modules for name in module_names if isinstance(name, str))


def _build_permission_map(permissions_data: list[dict]) -> dict:
    permission_map = {}
    for permission in permissions_data:
        description = _permission_menu_display_description(permission) or ""
        values = {
            item["value"]: item["label"]
            for item in _normalize_menu_permissions(
                permission.get("Permissions"),
                permission.get("model"),
                description,
            )
        }
        permission_map[permission["model"]] = {
            "description": description,
            "values": values,
        }
    return permission_map


def _build_structured_permissions(role_permissions: list[str] | None, permissions_data: list[dict]) -> list[dict]:
    structured_by_model = {}
    seen_by_model = {}

    def append_action(model: str, description: str, action_value: str, label: str) -> None:
        if model not in structured_by_model:
            structured_by_model[model] = {
                "model": model,
                "model_description": description,
                "sp": [],
            }
            seen_by_model[model] = set()

        if action_value not in seen_by_model[model]:
            structured_by_model[model]["sp"].append({action_value: label})
            seen_by_model[model].add(action_value)

    normalized_role_permissions = [
        normalized
        for permission_value in (role_permissions or [])
        if (normalized := _normalize_permission_value(permission_value))
    ]

    for permission in permissions_data:
        model = permission["model"]
        description = _permission_menu_display_description(permission) or ""
        for item in _normalize_menu_permissions(permission.get("Permissions"), model, description):
            actual_permission = item.get("permission") or f"{model}:{item['value']}"
            if any(
                _role_permission_matches(role_permission, actual_permission)
                for role_permission in normalized_role_permissions
            ):
                action_value = item.get("permission") or item["value"]
                append_action(model, description, action_value, item["label"])

    return list(structured_by_model.values())


@role_bp.post("/add")
@openapi.definition(
    summary="新建角色",
    description="新建一个角色，需要角色创建权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "description": "角色名"},
                    "description": {"type": "string", "description": "描述"},
                    "permissions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "权限列表",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "创建成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "name": {"type": "string"},
                                    "description": {"type": "string"},
                                    "permissions": {
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
@requires_permission("role:c")
async def add_role(request):
    name = request.json.get("name")
    description = request.json.get("description", "")
    permissions = _with_implicit_tool_view_permission(request.json.get("permissions", []))

    tenant_id = request.ctx.session.get("tenant_id")

    async with in_transaction():
        role = await Role.create(name=name, description=description, permissions=permissions, tenant_id=tenant_id)
    return response_ok(role.json)


@role_bp.post("/all")
@openapi.definition(
    summary="查找全部角色",
    description="查找全部角色，需要角色查看权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码"},
                    "size": {"type": "integer", "description": "页容量"},
                },
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "roles": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                                "name": {"type": "string"},
                                                "description": {"type": "string"},
                                                "permissions": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                            },
                                        },
                                    },
                                    "total": {"type": "integer"},
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total_pages": {"type": "integer"},
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("role:r")
async def find_all(request):
    page = max(1, int(request.json.get("page", 1)))
    size = max(1, min(int(request.json.get("size", 15)), 100))
    offset = (page - 1) * size

    tenant_id = request.ctx.session.get("tenant_id")

    roles = await Role.filter(deleted=False, tenant_id=tenant_id).offset(offset).limit(size).values()
    total = await Role.filter(deleted=False, tenant_id=tenant_id).count()
    return response_ok(
        {
            "roles": roles,
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
        }
    )


@role_bp.post("/details")
@openapi.definition(
    summary="查找具体角色信息",
    description="查找具体角色信息，需要角色查看权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["role_id"],
                "properties": {"role_id": {"type": "string", "description": "角色id"}},
            }
        }
    },
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "name": {"type": "string"},
                                    "description": {"type": "string"},
                                    "permissions": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "model": {"type": "string"},
                                                "model_description": {"type": "string"},
                                                "sp": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "additionalProperties": {"type": "string"},
                                                    },
                                                },
                                            },
                                        },
                                    },
                                    "playbook_permission_control": {
                                        "type": "string",
                                        "enum": ["controlled", "uncontrolled"],
                                        "description": "单剧本细粒度权限控制模式",
                                    },
                                    "playbook_permission_controls": {
                                        "type": "object",
                                        "additionalProperties": {
                                            "type": "string",
                                            "enum": ["controlled", "uncontrolled"],
                                        },
                                        "description": "按剧本管理具体权限项配置的单剧本细粒度权限控制模式",
                                    },
                                    "tool_resource_permission_control": {
                                        "type": "string",
                                        "enum": ["controlled", "uncontrolled"],
                                        "description": "应用资源执行权限控制模式",
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
@requires_permission("role:r")
async def find_role(request):
    role_id = request.json.get("role_id")

    tenant_id = request.ctx.session.get("tenant_id")

    role = await Role.get_or_none(id=role_id, deleted=False, tenant_id=tenant_id)
    if not role:
        raise CustomError(ErrorCode.ROLE_NOT_FOUND)

    permissions_data = await PermissionsMenu.filter(deleted=False).values()

    data = role.json
    data["permissions"] = _build_structured_permissions(role.permissions, permissions_data)
    data["playbook_permission_control"] = _playbook_permission_control(
        role.permissions
    )
    data["playbook_permission_controls"] = _playbook_permission_controls(
        role.permissions
    )
    data["tool_resource_permission_control"] = _tool_resource_permission_control(
        role.permissions
    )
    return response_ok(data)


@role_bp.post("/delete")
@openapi.definition(
    summary="删除指定角色",
    description="删除指定角色，需要角色删除权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["role_id"],
                "properties": {"role_id": {"type": "string", "description": "角色id"}},
            }
        }
    },
    response={
        "200": {
            "description": "删除成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("role:d")
async def delete_role(request):
    role_id = request.json.get("role_id")
    exists = await Account.filter(roles__id=role_id).exists()
    if exists:
        raise CustomError(ErrorCode.ROLE_ALREADY_USED)

    tenant_id = request.ctx.session.get("tenant_id")

    role = await Role.get_or_none(id=role_id, deleted=False, tenant_id=tenant_id)
    if not role:
        # 保留原有跨租户保护：请求命中的若是全平台超管角色，返回不可删除而非"不存在"
        cross_tenant = await Role.get_or_none(id=role_id, deleted=False)
        if cross_tenant and is_superadmin_role(cross_tenant):
            raise CustomError(ErrorCode.ROLE_CANNOT_DELETE)
        raise CustomError(ErrorCode.ROLE_NOT_FOUND)
    # 全平台超管角色不允许被删除。
    if is_superadmin_role(role):
        raise CustomError(ErrorCode.ROLE_CANNOT_DELETE)
    role.deleted = True
    await role.save()
    return response_ok("角色删除成功")


@role_bp.post("/update")
@openapi.definition(
    summary="修改角色内容",
    description="修改角色内容，需要角色修改权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["role_id"],
                "properties": {
                    "role_id": {"type": "string", "description": "角色id"},
                    "name": {"type": "string", "description": "新的角色名"},
                    "description": {"type": "string", "description": "新的描述"},
                    "permissions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "新的权限",
                    },
                },
            }
        }
    },
    response={
        "200": {
            "description": "更新成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "ctime": {"type": "string"},
                                    "utime": {"type": "string"},
                                    "name": {"type": "string"},
                                    "description": {"type": "string"},
                                    "permissions": {
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
@requires_permission("role:u")
async def update_role(request):
    role_id = request.json.get("role_id")
    name = request.json.get("name")
    description = request.json.get("description", "")
    permissions = _with_implicit_tool_view_permission(request.json.get("permissions", []))

    tenant_id = request.ctx.session.get("tenant_id")

    role = await Role.get_or_none(id=role_id, deleted=False, tenant_id=tenant_id)
    if not role:
        raise CustomError(ErrorCode.ROLE_NOT_FOUND)
    if name:
        role.name = name
    if description:
        role.description = description
    if permissions:
        role.permissions = _preserve_superadmin_permission(role, permissions)
    await role.save()
    return response_ok(role.json)


@role_bp.post("/permission/all")
@openapi.definition(
    summary="查找全部权限",
    description="查找全部权限，需要角色查看权限",
    response={
        "200": {
            "description": "获取成功",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "integer"},
                            "data": {
                                "type": "object",
                                "properties": {
                                    "permissions": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                                "model": {"type": "string"},
                                                "bp": {"type": "string"},
                                                "description": {"type": "string"},
                                                "Permissions": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "value": {"type": "string"},
                                                            "label": {"type": "string"},
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    }
                                },
                            },
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("role:r")
async def all_permission(request):
    lic = load_license()
    if not lic:
        raise CustomError(ErrorCode.INVALID_LICENSE_ERROR)
    valid_modules = _licensed_module_keys(lic)

    permission_list = []
    permissions = await PermissionsMenu.filter(deleted=False).values()
    for permission in _ordered_permission_menus(permissions):
        if _permission_menu_is_licensed(permission, valid_modules):
            permission_list.append(_normalize_permission_menu(permission))
    return response_ok(
        {
            "permissions": permission_list,
        }
    )
