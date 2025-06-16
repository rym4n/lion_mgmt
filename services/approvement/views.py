#!/usr/bin/env python
# coding=utf-8
from datetime import datetime

from sanic import Blueprint
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission
from models import Approvement
from settings import KAFKA_SERVERS, TASK_TOPIC

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
        status: 处理状态，0代表待处理，1代表通过，2代表拒绝
        expired: 布尔类型，True 代表查询包含已过期的结果，可选参数
    """
    params = request.json
    page = int(params.get("page", 1))
    size = int(params.get("size", 10))
    offset = (page - 1) * size
    condition = Q()
    keyword = params.get("keyword")
    status = params.get("status")
    expired = params.get("expired")
    if keyword:
        condition = Q(title__icontains=keyword) | Q(content__icontains=keyword)
    if status:
        condition = condition & Q(status=status)
    if expired is not True:
        condition = condition & Q(expire_time__gt=datetime.now())

    total = await Approvement.filter(condition).count()
    records = []
    if total > 0:
        records = await Approvement.filter(condition).offset(offset).limit(size).all().prefetch_related("approvers", "processors")
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
    # 查询 Approvement 表中 status 为 0 且 approvers 中包含当前用户 ID 的记录 且 expire_time 大于当前时间 且 processors 中不包含当前用户 ID
    condition = Q(status=0, approvers__contains=[user.id], expire_time__gt=datetime.now(), processors__not__contains=[user.id])
    total = await Approvement.filter(condition).count()
    records = await Approvement.filter(condition).offset((page - 1) * size).limit(size).all()
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
    发送审核结果（需要approve:u权限）

    请求参数:
        id: 审核id，必填参数
        agree: 布尔类型：True/False，同意或拒绝，必填参数
        remark: 审批备注信息
        params: 自定义参数的内容信息, 例如 {"key": "value"}
    """
    approvement_id = request.json.get("id")
    agree = request.json.get("agree")
    if not approvement_id or not isinstance(agree, bool):
        raise CustomError(ErrorCode.ParameterError)

    record = await Approvement.get(id=approvement_id, status=0, expire_time__gt=datetime.now()).prefetch_related("approvers", "processors")
    do_action = False
    record.processors.add(request.ctx.session['account_info'])

    if agree is False:
        record.status = 2
        do_action = True
    else:
        if record.approve_type == 2:
            # 如果是会签，只有所有审批人都同意了，才进行下一步
            if len(record.approvers) == len(record.processors):
                record.status = 1
                do_action = True
        else:
            record.status = 1
            do_action = True

    await record.save()

    if do_action:
        msg = {
            "run_type": 'flow', "run_id": record.run_id,
            "run_args": {
                "flow_id": record.flow_id, "flow_input": "", "approval_node_id": record.audit_node, "next_node_id": record.next_node,
                "approval_status": record.status, "executor": ",".join([p.id for p in record.processors])
            }
        }
        await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)

    return response_ok(record.json)
