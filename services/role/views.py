# coding=utf-8
from sanic import Blueprint
from sanic_ext import openapi
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import check_wildcard, is_superadmin_role, load_license, requires_permission
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


def _normalize_permission_value(permission_value: object) -> str | None:
    if not isinstance(permission_value, str) or ":" not in permission_value:
        return None
    if permission_value.startswith("agent:report:schedule:"):
        return f"agent_report_schedule:{permission_value.removeprefix('agent:report:schedule:')}"
    return LEGACY_PERMISSION_REMAP.get(permission_value, permission_value)


def _normalize_menu_action_value(permission_value: object) -> str | None:
    if not isinstance(permission_value, str) or not permission_value:
        return None
    return permission_value.rsplit(":", 1)[-1]


def _normalize_menu_permissions(permissions: object, menu_model: str | None = None) -> list[dict]:
    if not isinstance(permissions, list):
        return []

    normalized_permissions = []
    for item in permissions:
        if isinstance(item, dict):
            raw_value = item.get("value")
        else:
            raw_value = item
        value = _normalize_menu_action_value(raw_value)
        label = item.get("label") if isinstance(item, dict) else value
        if value:
            normalized_item = {"value": value, "label": label or value}
            actual_permission = _normalize_permission_value(raw_value)
            if actual_permission and menu_model and actual_permission.rsplit(":", 1)[0] != menu_model:
                normalized_item["permission"] = actual_permission
            normalized_permissions.append(normalized_item)
    return normalized_permissions


def _normalize_permission_menu(permission: dict) -> dict:
    normalized = dict(permission)
    normalized["Permissions"] = _normalize_menu_permissions(
        permission.get("Permissions"),
        permission.get("model"),
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
    return permission.get("description") in valid_modules or permission.get("bp") in valid_modules


def _build_permission_map(permissions_data: list[dict]) -> dict:
    permission_map = {}
    for permission in permissions_data:
        values = {
            item["value"]: item["label"]
            for item in _normalize_menu_permissions(permission.get("Permissions"), permission.get("model"))
        }
        permission_map[permission["model"]] = {
            "description": permission["description"],
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
        description = permission["description"]
        for item in _normalize_menu_permissions(permission.get("Permissions"), model):
            action_value = item.get("permission") or item["value"]
            actual_permission = item.get("permission") or f"{model}:{item['value']}"
            if any(check_wildcard(role_permission, actual_permission) for role_permission in normalized_role_permissions):
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
    permissions = request.json.get("permissions", [])

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

    permissions_data = await PermissionsMenu.all().values()

    data = role.json
    data["permissions"] = _build_structured_permissions(role.permissions, permissions_data)
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
        # 保留原有跨租户保护：请求命中的若是超管角色（在默认租户里），返回不可删除而非"不存在"
        cross_tenant = await Role.get_or_none(id=role_id, deleted=False)
        if cross_tenant and is_superadmin_role(cross_tenant):
            raise CustomError(ErrorCode.ROLE_CANNOT_DELETE)
        raise CustomError(ErrorCode.ROLE_NOT_FOUND)
    # 超管角色（拥有 *:* 权限的那条）不允许被删除——按权限本质判定而非硬编码 id
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
    permissions = request.json.get("permissions", [])

    tenant_id = request.ctx.session.get("tenant_id")

    role = await Role.get_or_none(id=role_id, deleted=False, tenant_id=tenant_id)
    if not role:
        raise CustomError(ErrorCode.ROLE_NOT_FOUND)
    if name:
        role.name = name
    if description:
        role.description = description
    if permissions:
        role.permissions = permissions
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
    permissions = await PermissionsMenu.all().values()
    for permission in permissions:
        if _permission_menu_is_licensed(permission, valid_modules):
            permission_list.append(_normalize_permission_menu(permission))
    return response_ok(
        {
            "permissions": permission_list,
        }
    )
