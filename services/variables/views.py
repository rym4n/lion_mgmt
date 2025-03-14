#!/usr/bin/env python
# coding=utf-8

from sanic import Blueprint

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.variables import Variables

var_bp = Blueprint('var', url_prefix='v1/var')
_all__ = ["var_bp"]


@var_bp.post("/new")
@requires_permission("var:c")
async def new_user(request):
    """
    新建全局变量

    请求参数
        name: 变量名
        type: 变量类型，枚举型（string 或 list)
        value: 变量值
    """
    name = request.json.get("name")
    type = request.json.get("type")
    value = request.json.get("value")
    if not name or not type or not value:
        raise CustomError(ErrorCode.ParameterError)

    # 如果type不是list，需要检查name是否已存在
    if type != "list":
        var = await Variables.filter(name=name).first()
        if var:
            raise CustomError(ErrorCode.DataAlreadyExists)

    var = await Variables.create(name=request.json["name"], type=request.json["type"], value=request.json["value"])
    return response_ok(var.json)


@var_bp.post("/search")
@requires_permission("var:r")
async def query_user(request):
    """
    搜索全局变量

    请求参数：
        name: 变量名
    """
    var = await Variables.filter(name=request.json.get("name")).first()
    if not var:
        raise CustomError(ErrorCode.AccountNotFound)
    return response_ok(var.json)


@var_bp.post("/list")
@requires_permission("var:r")
async def all_users(request):
    """
    获取所有全局变量信息
    """
    vars = await Variables.group_by("name", "type").all()
    return response_ok([var.json for var in vars])


@var_bp.post("/del")
@requires_permission("var:d")
async def delelte_user(request):
    """
    删除全局变量（需要var:d权限）

    请求参数:
        name: 变量名
    """
    var = await Variables.filter(name=request.json.get("name")).first()
    if not var:
        raise CustomError(ErrorCode.AccountNotFound)
    await var.delete()
    return response_ok("变量删除成功")


@var_bp.post("/update")
@requires_permission("var:u")
async def update_user(request):
    """
    更新全局变量（需要var:u权限）

    请求参数:
        name: 变量名
        value: 变量值
    """
    var = await Variables.filter(name=request.json.get("name")).first()
    if not var:
        raise CustomError(ErrorCode.AccountNotFound)
    var.value = request.json.get("value")
    await var.save()
    return response_ok("变量更新成功")
