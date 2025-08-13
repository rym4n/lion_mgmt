#!/usr/bin/env python
# coding=utf-8
from datetime import datetime

import pytz
from sanic import Blueprint
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission
from models import Approvement
from settings import KAFKA_SERVERS, TASK_TOPIC

approve_bp = Blueprint('approve', url_prefix='/approve')
_all__ = ["approve_bp"]


@approve_bp.post("/list")
@requires_permission("approve:r")
async def all_approvement(request):
    """
    获取审批人为当前用户的审批任务信息（需要approve:r权限）
    
    请求参数：
        page: 页码
        size: 每页数量
        keyword: 关键字，模糊搜索审核标题或者审核内容，可选参数
        status: 处理状态，list类型，支持同时过滤多个状态，0代表待处理，1代表通过，2代表拒绝，3代表已过期
    """
    params = request.json
    page = int(params.get("page", 1))
    size = int(params.get("size", 15))
    offset = (page - 1) * size

    condition = Q(approvers=request.ctx.session['account_info'].id)
    keyword = params.get("keyword")
    status = params.get("status")

    if keyword:
        condition = Q(title__icontains=keyword) | Q(content__icontains=keyword)
    if status:
        condition = condition & Q(status__in=status)

    await Approvement.filter(status=0, expire_time__lt=datetime.now()).update(status=3)
    total = await Approvement.filter(condition).count()
    records = []
    if total > 0:
        records = await Approvement.filter(condition).offset(offset).limit(size).prefetch_related("flow", "approvers").all()

    return response_ok({
        "total": total,
        "page": page,
        "size": size,
        "total_pages": (total + size - 1) // size,
        "data": [r.json for r in records]
    })


@approve_bp.post("/todolist")
async def todo_approvement(request):
    """
    获取当前登录用户的待处理任务信息（需要approve:r权限）
    主要用于右上角的待办提醒
    """
    # 修改过期任务状态,后端就不做定时任务了
    await Approvement.filter(status=0, expire_time__lt=datetime.now()).update(status=3)

    # 将created_at从UTC时间转换为北京时间（UTC+8）
    sql = f"""
          SELECT a.title, a.content, a.expire_time, a.created_at, a.approve_type
          FROM approvement a
          JOIN approve_accounts b ON a.id = b.approvement_id AND b.account_id = '{request.ctx.session["sub"]}'
          WHERE a.status = 0 AND NOT a.results ? '{request.ctx.session["sub"]}'
        """
    async with in_transaction() as conn:
        rows = await conn.execute_query_dict(sql)
        for row in rows:
            row["created_at"] = row["created_at"].astimezone(pytz.timezone('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            row["expire_time"] = row["expire_time"].astimezone(pytz.timezone('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        return response_ok(rows)


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
    param = request.json
    approvement_id = param.get("id")
    agree = param.get("agree")
    remark = param.get("remark", "")
    params = param.get("params", {})
    if not approvement_id or not isinstance(agree, bool) or not isinstance(remark, str) or not isinstance(params, list):
        raise CustomError(ErrorCode.ParameterError)

    approve = await Approvement.filter(id=approvement_id).select_for_update().prefetch_related("approvers").first()
    if not approve:
        raise CustomError(ErrorCode.NotFound)

    if approve.status != 0:
        raise CustomError(ErrorCode.ApprovementStatusError)

    if params:
        approve.params = params
    account_info = request.ctx.session['account_info']
    approve.results = {
        **approve.results,
        account_info.id: {
            "username": account_info.username,
            "display_name": account_info.display_name,
            "agree": agree,
            "remark": remark,
            "approve_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    }
    do_action = False

    if agree:
        if approve.approve_type == 2:
            # 如果是会签，只有所有审批人都同意了，才进行下一步
            if len(approve.results) == len(approve.approvers):
                approve.status = 1
                do_action = True
        else:
            approve.status = 1
            do_action = True
    else:
        approve.status = 2

    await approve.save()

    if do_action:
        msg = {
            "run_type": 'flow', "run_id": approve.run_id,
            "run_args": {
                "flow_id": approve.flow_id, "flow_input": params, "approval_node_id": approve.audit_node, "next_node_id": approve.next_node, "approval_status": approve.status,
                "executor": ",".join([p.id for p in approve.approvers]) if approve.approve_type == 2 else request.ctx.session['account_info'].id
            }
        }
        await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)

    return response_ok('success')
