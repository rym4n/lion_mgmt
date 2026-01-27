# coding=utf-8
import time

from sanic import Blueprint
from sanic_ext import openapi
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import load_license, requires_permission
from models import Account, PermissionsMenu, Role

role_bp = Blueprint("role", url_prefix="/role")
_all__ = ["role_bp"]


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

    permission_map = {
        p["model"]: {
            "description": p["description"],
            "values": {perm["value"]: perm["label"] for perm in p.get("Permissions", [])},
        }
        for p in permissions_data
    }

    structured_permissions = []

    for perm_str in role.permissions:
        model, actions = perm_str.split(":")

        perm_info = permission_map.get(model)
        if not perm_info:
            continue

        action_labels = []
        for action in actions:
            label = perm_info["values"].get(action)
            if label:
                action_labels.append({action: label})

        structured_permissions.append(
            {
                "model": model,
                "model_description": perm_info["description"],
                "sp": action_labels,
            }
        )

    data = role.json
    data["permissions"] = structured_permissions
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

    if role_id == "111111":
        raise CustomError(ErrorCode.ROLE_CANNOT_DELETE)

    tenant_id = request.ctx.session.get("tenant_id")

    role = await Role.get_or_none(id=role_id, deleted=False, tenant_id=tenant_id)
    if not role:
        raise CustomError(ErrorCode.ROLE_NOT_FOUND)
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
    menu_dict = {}
    lic = load_license()
    if not lic:
        raise CustomError(ErrorCode.INVALID_LICENSE_ERROR)
    for item in lic["moduleList"]:
        name = item["name"]
        auth = item["auth"]

        if name not in menu_dict:
            menu_dict[name] = False
        if auth and int(lic["grouplist"][item["group"]]["expireTime"]) > int(time.time() * 1000):
            menu_dict[name] = True
    menu_dict["许可管理"] = True

    permission_list = []
    permissions = await PermissionsMenu.all().values()
    for permission in permissions:
        if permission["description"] in menu_dict:
            permission_list.append(permission)
    return response_ok(
        {
            "permissions": permission_list,
        }
    )
