#!/usr/bin/env python
# coding=utf-8
from datetime import datetime

import orjson
import pytz
from sanic import Blueprint
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from libs.security.authorization import requires_permission
from models import Approvement
from settings import KAFKA_SERVERS, TASK_TOPIC, TZ

approve_bp = Blueprint("approve", url_prefix="/approve")
_all__ = ["approve_bp"]


@approve_bp.post("/list")
@openapi.definition(
    summary="获取审批人为当前用户的审批任务信息",
    description="获取审批人为当前用户的审批任务信息，需要approve:r权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码"},
                    "size": {"type": "integer", "description": "每页数量"},
                    "keyword": {"type": "string", "description": "关键字，模糊搜索审核标题或者审核内容"},
                    "status": {"type": "array", "items": {"type": "integer"}, "description": "处理状态，支持同时过滤多个状态，0代表待处理，1代表通过，2代表拒绝，3代表已过期"},
                    "ctime": {"type": "array", "items": {"type": "string"}, "description": "搜索时使用，支持搜索创建时间", "example": ["2021-01-01T00:00:00Z", "2021-01-31T23:59:59Z"]},
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
                                    "total": {"type": "integer"},
                                    "page": {"type": "integer"},
                                    "size": {"type": "integer"},
                                    "total_pages": {"type": "integer"},
                                    "data": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "ctime": {"type": "string"},
                                                "utime": {"type": "string"},
                                                "title": {"type": "string"},
                                                "run_id": {"type": "string"},
                                                "content": {"type": "string"},
                                                "status": {"type": "integer"},
                                                "approve_type": {"type": "integer"},
                                                "expire_time": {"type": "string"},
                                                "audit_node": {"type": "string"},
                                                "created_at": {"type": "string"},
                                                "params": {"type": "array"},
                                                "results": {"type": "object"},
                                                "flow_id": {"type": "string"},
                                                "approvers": {
                                                    "type": "array",
                                                    "items": {"type": "object", "properties": {"id": {"type": "string"}, "username": {"type": "string"}, "display_name": {"type": "string"}}},
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
@requires_permission("approve:r")
async def all_approvement(request):
    params = request.json
    page = int(params.get("page", 1))
    size = int(params.get("size", 15))
    offset = (page - 1) * size

    condition = Q(approvers=request.ctx.session["account_info"].id)
    keyword = params.get("keyword")
    status = params.get("status")
    ctime = params.get("ctime")

    if keyword:
        condition = Q(title__icontains=keyword) | Q(content__icontains=keyword)
    if status:
        condition = condition & Q(status__in=status)

    if ctime and len(ctime) == 2:
        condition = condition & Q(ctime__range=[datetime.strptime(TZ.localize(t), "%Y-%m-%d %H:%M:%S") for t in ctime])

    await Approvement.filter(status=0, expire_time__lt=datetime.now()).update(status=3)
    total = await Approvement.filter(condition).count()
    records = []
    if total > 0:
        records = await Approvement.filter(condition).offset(offset).limit(size).prefetch_related("flow", "approvers").all()

    return response_ok({"total": total, "page": page, "size": size, "total_pages": (total + size - 1) // size, "data": [r.json for r in records]})


@approve_bp.post("/todolist")
@openapi.definition(
    summary="获取当前登录用户的待处理任务信息",
    description="获取当前登录用户的待处理任务信息，主要用于右上角的待办提醒",
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
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "content": {"type": "string"},
                                        "expire_time": {"type": "string"},
                                        "created_at": {"type": "string"},
                                        "approve_type": {"type": "integer"},
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
async def todo_approvement(request):
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
            row["created_at"] = row["created_at"].astimezone(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
            row["expire_time"] = row["expire_time"].astimezone(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        return response_ok(rows)


@approve_bp.post("/update")
@openapi.definition(
    summary="发送审核结果",
    description="发送审核结果，需要approve:u权限",
    body={
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["id", "agree"],
                "properties": {
                    "id": {"type": "string", "description": "审核id"},
                    "agree": {"type": "boolean", "description": "布尔类型：True/False，同意或拒绝"},
                    "remark": {"type": "string", "description": "审批备注信息"},
                    "params": {"type": "array", "items": {"type": "object"}, "description": "自定义参数的内容信息"},
                },
            }
        }
    },
    response={"200": {"description": "更新成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer"}, "data": {"type": "string"}}}}}}},
)
@requires_permission("approve:u")
async def update_approvement(request):
    param = request.json
    approvement_id = param.get("id")
    agree = param.get("agree")
    remark = param.get("remark", "")
    params = param.get("params") or []
    if not approvement_id or not isinstance(agree, bool) or not isinstance(remark, str) or not isinstance(params, list):
        raise CustomError(ErrorCode.ParameterError)

    approve = await Approvement.filter(id=approvement_id).select_for_update().prefetch_related("approvers").first()
    if not approve:
        raise CustomError(ErrorCode.NotFound)

    if approve.status != 0:
        raise CustomError(ErrorCode.ApprovementStatusError)

    if params:
        approve.params = params
    account_info = request.ctx.session["account_info"]
    approve.results = {
        **approve.results,
        account_info.id: {"username": account_info.username, "display_name": account_info.display_name, "agree": agree, "remark": remark, "approve_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
    }

    if agree:
        if approve.approve_type == 2:
            # 如果是会签，只有所有审批人都同意了，才进行下一步
            if len(approve.results) == len(approve.approvers):
                approve.status = 1
        else:
            approve.status = 1
    else:
        approve.status = 2

    await approve.save()

    msg = {
        "run_type": "flow",
        "run_id": approve.run_id,
        "run_args": {
            "flow_id": approve.flow_id,
            "flow_input": params,
            "approval_node_id": approve.audit_node,
            "approval_status": approve.status,
            "executor": ",".join([p.id for p in approve.approvers]) if approve.approve_type == 2 else request.ctx.session["account_info"].id,
        },
    }
    await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)

    return response_ok("success")


@approve_bp.get("/callback", name="approve_callback_get")
@openapi.definition(
    summary="外部系统审核结果回调",
    description="接受三方系统的审核结果，同时支持POST/GET",
    parameters=[
        {"name": "token", "in": "query", "description": "审核的唯一token", "required": True, "schema": {"type": "string"}},
        {"name": "agree", "in": "query", "description": "布尔类型：True/False，同意或拒绝", "required": True, "schema": {"type": "boolean"}},
        {"name": "remark", "in": "query", "description": "审批备注信息", "required": False, "schema": {"type": "string"}},
        {"name": "params", "in": "query", "description": "自定义参数的内容信息，json格式字符串", "required": False, "schema": {"type": "string"}},
    ],
    response={"200": {"description": "更新成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"code": {"type": "integer"}, "data": {"type": "string"}}}}}}},
)
@requires_permission("approve:u")
async def update_approvement(request):
    param = dict(request.args)

    token = param.get("token")
    agree = param.get("agree")
    remark = param.get("remark", "")
    flow_input = param.get("params")

    if not token or not isinstance(agree, bool) or not isinstance(remark, str):
        raise CustomError(ErrorCode.ParameterError)

    if flow_input:
        try:
            flow_input = orjson.loads(flow_input)
        except orjson.JSONDecodeError:
            raise CustomError(ErrorCode.ParameterError)

    approve = await Approvement.filter(callback_token=token).select_for_update().prefetch_related("approvers").first()
    if not approve:
        raise CustomError(ErrorCode.NotFound)

    if approve.status != 0:
        raise CustomError(ErrorCode.ApprovementStatusError)
    
    if flow_input:
        approve.params = flow_input

    approve.results = {**approve.results, token: {"username": "callback", "display_name": "外部回调", "agree": agree, "remark": remark, "approve_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}

    if agree:
        approve.status = 1
    else:
        approve.status = 2

    await approve.save()

    msg = {
        "run_type": "flow",
        "run_id": approve.run_id,
        "run_args": {
            "flow_id": approve.flow_id,
            "flow_input": flow_input,
            "approval_node_id": approve.audit_node,
            "approval_status": approve.status,
            "executor": ",".join([p.id for p in approve.approvers]),
        },
    }
    await aproduce(KAFKA_SERVERS, TASK_TOPIC, msg)

    return response_ok("success")
