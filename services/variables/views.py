#!/usr/bin/env python
# coding=utf-8

from sanic import Blueprint
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.variables import Variables

var_bp = Blueprint('var', url_prefix='v1/var')
_all__ = ["var_bp"]


@var_bp.post("/new")
@requires_permission("var:c")
async def new_variable(request):
    """
    新建全局变量

    请求参数
        name: 变量名（唯一）
        value: 变量值，JSON格式对象，不是字符串
    """
    name = request.json.get("name")
    value = request.json.get("value")
    if not name or not value:
        raise CustomError(ErrorCode.ParameterError)

    var = await Variables.create(name=request.json["name"], value=value, updator=request.ctx.session['account_info'])
    return response_ok(var.json)


@var_bp.post("/list")
@requires_permission("var:r")
async def all_variables(request):
    """
    获取所有全局变量信息（需要var:r权限）
    
    请求参数：
        page: 页码
        size: 每页数量
        keyword: 可选参数，变量名 或 变量值（元素要完全匹配）
    """
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 10))
    offset = (page - 1) * size
    limit = size
    condition = Q()
    keyword = request.json.get("keyword")
    if keyword:
        condition = Q(name__icontains=keyword) | Q(value__contains=[keyword])
    total = await Variables.filter(condition).count()
    records = []
    if total > 0:
        records = await Variables.filter(condition).offset(offset).limit(limit).all().prefetch_related("updator")
    return response_ok({"total": total, "data": [v.json for v in records]})


@var_bp.post("/del")
@requires_permission("var:d")
async def delelte_variable(request):
    """
    删除全局变量（需要var:d权限）

    请求参数:
        name: 变量名
    """
    var = await Variables.filter(name=request.json.get("name")).first()
    if not var:
        raise CustomError(ErrorCode.NotFound)
    await var.delete()
    return response_ok("success")


@var_bp.post("/update")
@requires_permission("var:u")
async def update_variable(request):
    """更新全局变量（需要var:u权限）"""
    name = request.json.get("name")
    value = request.json.get("value")
    if not name or not value:
        raise CustomError(ErrorCode.ParameterError)

    var = await Variables.filter(name=name).first()
    if not var:
        raise CustomError(ErrorCode.NotFound)

    var.value = value
    var.updator = request.ctx.session['account_info']
    await var.save()
    return response_ok("success")
