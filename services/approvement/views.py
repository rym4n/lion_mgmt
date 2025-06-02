#!/usr/bin/env python
# coding=utf-8

from sanic import Blueprint
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models import Approvement

approve_bp = Blueprint('approve', version=1, url_prefix='/approve')
_all__ = ["approve_bp"]


@approve_bp.post("/list")
@requires_permission("approve:r")
async def all_approvement(request):
    """
    获取所有人工审批任务信息（需要approve:r权限）
    
    请求参数：
        page: 页码
        size: 每页数量
        keyword: 关键字，模糊搜索审核标题或者审核内容，可选参数
    """
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 10))
    offset = (page - 1) * size
    limit = size
    condition = Q()
    keyword = request.json.get("keyword")
    if keyword:
        condition = Q(title__icontains=keyword) | Q(content__icontains=keyword)
    total = await Approvement.filter(condition).count()
    records = []
    if total > 0:
        records = await Approvement.filter(condition).offset(offset).limit(limit).all().prefetch_related("processor")
    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": [a.json for a in records]
    })


@approve_bp.post("/todolist")
@requires_permission("approve:r")
async def todo_approvement(request):
    """
    获取当前登录用户的待处理任务信息（需要approve:r权限）
    主要用于右上角的待办提醒

    请求参数：
        page: 页码
        size: 每页数量
        keyword: 关键字，模糊搜索审核标题或者审核内容，可选参数
    """
    page = int(request.json.get("page", 1))
    size = int(request.json.get("size", 10))

    user = request.ctx.session['account_info']
    # 查询 Approvement 表中 status 为 0 且 approvers 中包含当前用户 ID 的记录
    total = await Approvement.filter(status=0, approvers__contains=[user.sub]).count()
    records = await Approvement.filter(status=0, approvers__contains=[user.sub]).offset((page - 1) * size).limit(size).all()
    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": [{
            "id": a.id,
            "title": a.title,
            "content": a.content,
            "expire_time": a.expire_time
        } for a in records]
    })


@approve_bp.post("/update")
@requires_permission("approve:u")
async def update_approvement(request):
    """
    更新全局变量（需要approve:u权限）

    请求参数:
        id: 审核id，必填参数
        agree: true/false，同意或拒绝，必填参数
        remark: 审批备注信息
        params: 自定义参数的内容信息, 例如 {"aaa": "bbb"}
    """
    approvement_id = request.json.get("id")
    agree = request.json.get("agree")
    if not approvement_id or not agree:
        raise CustomError(ErrorCode.ParameterError)

    agree = bool(agree)
    record = await Approvement.get(id=approvement_id)
    record.status = 1 if agree else 2
    record.processor = request.ctx.session['account_info']
    await record.save()
    return response_ok(record.json)
