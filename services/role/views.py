# coding=utf-8

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.utils import get_ip
from models import Role
from sanic import Blueprint
from sanic.log import logger
from tortoise.exceptions import ValidationError, IntegrityError
from tortoise.transactions import in_transaction

role_bp = Blueprint('role', url_prefix='role')
_all__ = ["role_bp"]


@role_bp.route("/add", methods=["POST"])
async def add_role(request):
    """新建角色"""
    try:
        name = request.json.get("name")
        description = request.json.get("description", "")
        permissions = request.json.get("permissions", [])
        async with in_transaction():
            role = await Role.create(name=name, description=description, permissions=permissions)
        logger.info(f"Client {get_ip(request)} has added a new role {role.id}.")
        return response_ok(role.json)
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        raise CustomError(ErrorCode.ParameterError)
    except IntegrityError as e:
        if "UNIQUE constraint failed" in str(e):
            raise CustomError(ErrorCode.RoleAlreadyExists)
        raise CustomError(ErrorCode.ServerError)


@role_bp.route("/all", methods=["GET"])
async def find_all(request):
    """查找全部角色"""
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 10))
        offset = (page - 1) * page_size
        limit = page_size
        roles = await Role.filter(deleted=False).offset(offset).limit(limit).values()
        total = await Role.filter(deleted=False).count()
        return response_ok({
            'roles': roles,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        })
    except IntegrityError as e:
        raise CustomError(ErrorCode.ServerError)


@role_bp.route("/<role_id>", methods=["GET"])
async def find_role(request, role_id):
    """查找具体角色信息"""
    try:
        role = await Role.filter(id=role_id, deleted=False).first()
        if not role:
            raise CustomError(ErrorCode.RoleNotFound)
        return response_ok(role.json)
    except IntegrityError as e:
        raise CustomError(ErrorCode.ServerError)


@role_bp.route("/delete", methods=["POST"])
async def delete_role(request):
    """删除指定角色"""
    try:
        role_id = request.json.get("role_id")
        role = await Role.filter(id=role_id, deleted=False).first()
        if not role:
            raise CustomError(ErrorCode.RoleNotFound)
        role.deleted = True
        await role.save()
        return response_ok("角色删除成功")
    except IntegrityError as e:
        raise CustomError(ErrorCode.ServerError)


@role_bp.route("/update", methods=["POST"])
async def update_role(request):
    """修改角色内容"""
    try:
        role_id = request.json.get("role_id")
        name = request.json.get("name")
        description = request.json.get("description", "")
        permissions = request.json.get("permissions", [])
        role = await Role.filter(id=role_id, deleted=False).first()
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
    except IntegrityError as e:
        raise CustomError(ErrorCode.ServerError)
    except ValidationError as e:
        raise CustomError(ErrorCode.ParameterError)
