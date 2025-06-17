# coding=utf-8

from sanic import Blueprint
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok, response_fail
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Role, Account, PermissionsMenu

role_bp = Blueprint('role', url_prefix='/role')
_all__ = ["role_bp"]


@role_bp.post("/add")
@requires_permission('role:c')
async def add_role(request):
    """
    新建角色(需要角色创建权限)

    请求参数：
        name:角色名
        description:描述
        permissions:权限  ["a:1"]
    """
    name = request.json.get("name")
    description = request.json.get("description", "")
    permissions = request.json.get("permissions", [])
    async with in_transaction():
        role = await Role.create(name=name, description=description, permissions=permissions)
    return response_ok(role.json)


@role_bp.post("/all")
@requires_permission('role:r')
async def find_all(request):
    """
    查找全部角色  (需要角色查看权限)

    请求参数：
        page:页码
        size:页容量
    """
    page = int(request.json.get('page', 1))
    size = int(request.json.get('size', 10))
    offset = (page - 1) * size
    limit = size
    roles = await Role.filter(deleted=False).offset(offset).limit(limit).values()
    total = await Role.filter(deleted=False).count()
    return response_ok({
        'roles': roles,
        'total': total,
        'page': page,
        'size': size,
        'total_pages': (total + size - 1) // size
    })


@role_bp.post("/details")
@requires_permission('role:r')
async def find_role(request):
    """
    查找具体角色信息    (需要角色查看权限)

    请求参数：
        role_id：角色id
    """
    role_id = request.json.get("role_id")
    role = await Role.get_or_none(id=role_id, deleted=False)
    if not role:
        raise CustomError(ErrorCode.RoleNotFound)
    return response_ok(role.json)


@role_bp.post("/delete")
@requires_permission('role:d')
async def delete_role(request):
    """
    删除指定角色    (需要角色删除权限)

    请求参数：
        role_id：角色id
    """
    role_id = request.json.get("role_id")
    exists = await Account.filter(roles__id=role_id).exists()
    if exists:
        return response_fail(ErrorCode.RoleAlreadyUsed)
    role = await Role.get_or_none(id=role_id, deleted=False)
    if not role:
        raise CustomError(ErrorCode.RoleNotFound)
    role.deleted = True
    await role.save()
    return response_ok("角色删除成功")


@role_bp.post("/update")
@requires_permission('role:u')
async def update_role(request):
    """
    修改角色内容    (需要角色修改权限)

    请求参数：
        role_id：角色id
        name：新的角色名
        description：新的描述
        permissions：新的权限
    """
    role_id = request.json.get("role_id")
    name = request.json.get("name")
    description = request.json.get("description", "")
    permissions = request.json.get("permissions", [])
    role = await Role.get_or_none(id=role_id, deleted=False)
    if not role:
        raise CustomError(ErrorCode.RoleNotFound)
    if name:
        role.name = name
    if description:
        role.description = description
    if permissions:
        role.permissions = permissions
    await role.save()
    return response_ok(role.json)


@role_bp.post("/permission/all")
@requires_permission('role:r')
async def all_permission(request):
    """
    查找全部权限    (需要角色查看权限)

    请求参数：
    """
    permissions = await PermissionsMenu.all().values()
    return response_ok({
        'permissions': permissions,
    })
