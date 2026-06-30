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
from settings import TASK_TOPIC

approve_bp = Blueprint("approve", url_prefix="/approve")
_all__ = ["approve_bp"]


def _agent_command_approval_id(approve: Approvement) -> str | None:
    params = approve.params if isinstance(approve.params, dict) else {}
    if params.get("approval_kind") != "agent":
        return None
    return params.get("command_approval_id")


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
                    "keyword": {
                        "type": "string",
                        "description": "关键字，模糊搜索审核标题或者审核内容",
                    },
                    "status": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "处理状态，支持同时过滤多个状态，0代表待处理，1代表通过，2代表拒绝，3代表已过期",
                    },
                    "ctime": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "搜索时使用，支持创建时间范围（UTC格式：2025-01-01T00:00:00Z）",
                        "example": ["2025-01-01T00:00:00Z", "2025-02-01T00:00:00Z"],
                    },
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
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {"type": "string"},
                                                            "username": {"type": "string"},
                                                            "display_name": {"type": "string"},
                                                        },
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
@requires_permission("approve:r")
async def all_approvement(request):
    params = request.json
    page = max(1, int(params.get("page", 1)))
    size = max(1, min(int(params.get("size", 15)), 100))
    offset = (page - 1) * size

    tenant_id = request.ctx.session.get("tenant_id")

    condition = Q(approvers=request.ctx.session["account_info"].id, tenant_id=tenant_id)
    keyword = params.get("keyword")
    status = params.get("status")
    ctime = params.get("ctime")

    if keyword:
        condition = Q(title__icontains=keyword) | Q(content__icontains=keyword)
    if status:
        condition = condition & Q(status__in=status)

    if ctime and len(ctime) == 2:
        condition = condition & Q(ctime__range=[datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC) for t in ctime])

    await Approvement.filter(status=0, expire_time__lt=datetime.now(), tenant_id=tenant_id).update(status=3)
    total = await Approvement.filter(condition).count()
    records = []
    if total > 0:
        records = await Approvement.filter(condition).offset(offset).limit(size).prefetch_related("flow", "approvers").all()

    return response_ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
            "data": [r.json for r in records],
        }
    )


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

    runlog_sql = """
        UPDATE runlog r
        SET status = 3,
            result = '审核已超时'
        FROM approvement a
        WHERE r.run_id = a.run_id
          AND r.node_id = a.audit_node
          AND r.node_type = 'approval-node'
          AND r.status = 2
          AND a.expire_time < $1
    """
    async with in_transaction() as conn:
        rows = await conn.execute_query_dict(sql)
        await conn.execute_query(runlog_sql, [datetime.now()])
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
                    "agree": {
                        "type": "boolean",
                        "description": "布尔类型：True/False，同意或拒绝",
                    },
                    "remark": {"type": "string", "description": "审批备注信息"},
                    "params": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "自定义参数的内容信息",
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
                            "data": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
@requires_permission("approve:u")
async def update_approvement(request):
    """
    更新审批任务状态并发送 Kafka 消息。

    参数:
        request: Sanic 请求对象，需包含 JSON 字段：
            - id (str): 审批任务 ID
            - agree (bool): 审批结果，True 表示通过，False 表示拒绝
            - remark (str, 可选): 审批备注
            - params (list, 可选): 自定义参数列表，每个元素需包含 name/value/required 字段

    返回:
        成功时返回 response_ok("success")

    异常:
        CustomError: 参数缺失或格式错误、审批任务不存在、任务状态非待处理
    """
    param = request.json
    approvement_id = param.get("id")
    agree = param.get("agree")
    remark = param.get("remark", "")
    args = param.get("params") or []
    if not approvement_id or not isinstance(agree, bool) or not isinstance(remark, str) or not isinstance(args, list):
        raise CustomError(ErrorCode.PARAM_ERROR)

    for p in args:
        if not p.get("name") or not p.get("value"):
            raise CustomError(ErrorCode.PARAM_ERROR)

        if p.get("required") is True and not p.get("value"):
            raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = request.ctx.session.get("tenant_id")

    approve = await Approvement.filter(id=approvement_id, tenant_id=tenant_id).select_for_update().prefetch_related("approvers").first()
    if not approve:
        raise CustomError(ErrorCode.NOT_FOUND)

    if approve.status != 0:
        raise CustomError(ErrorCode.APPROVEMENT_STATUS_ERROR)

    command_approval_id = _agent_command_approval_id(approve)
    if command_approval_id:
        from services.command_center.approval_service import decide_approval

        await decide_approval(
            tenant_id,
            command_approval_id,
            "approve" if agree else "reject",
            request.ctx.session["account_info"].id,
            remark,
        )
        return response_ok("success")

    if args:
        approve.params = args
    account_info = request.ctx.session["account_info"]
    approve.results = {
        **approve.results,
        account_info.id: {
            "username": account_info.username,
            "display_name": account_info.display_name,
            "agree": agree,
            "remark": remark,
            "approve_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
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

    flow_input = {p["name"]: p["value"] for p in args}
    flow_input["approval_result"] = approve.results

    msg = {
        "run_type": "flow",
        "run_id": approve.run_id,
        "run_args": {
            "flow_id": approve.flow_id,
            "flow_input": flow_input,
            "approval_node_id": approve.audit_node,
            "approval_status": approve.status,
            "executor": (",".join([p.id for p in approve.approvers]) if approve.approve_type == 2 else request.ctx.session["account_info"].id),
        },
        "tenant_id": tenant_id,
    }
    await aproduce(TASK_TOPIC, msg)

    return response_ok("success")


@approve_bp.get("/callback", name="approve_callback_get")
@openapi.definition(
    summary="外部系统审核结果回调",
    description="接受三方系统的审核结果，同时支持POST/GET",
    parameter=[
        {
            "name": "token",
            "in": "query",
            "description": "审核的唯一token",
            "required": True,
            "schema": {"type": "string"},
        },
        {
            "name": "agree",
            "in": "query",
            "description": "布尔类型：True/False，同意或拒绝",
            "required": True,
            "schema": {"type": "boolean"},
        },
        {
            "name": "remark",
            "in": "query",
            "description": "审批备注信息",
            "required": False,
            "schema": {"type": "string"},
        },
        {
            "name": "params",
            "in": "query",
            "description": "自定义参数的内容信息，json格式字符串",
            "required": False,
            "schema": {"type": "string"},
        },
    ],
    response={
        "200": {
            "description": "更新成功",
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
async def update_approvement_callback(request):
    token = request.args.get("token")
    agree_str = request.args.get("agree")
    remark = request.args.get("remark", "")
    flow_input = request.args.get("params")

    # 将字符串转换为布尔值
    if not token or not agree_str:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if agree_str.lower() in ("true", "1", "yes"):
        agree = True
    elif agree_str.lower() in ("false", "0", "no"):
        agree = False
    else:
        raise CustomError(ErrorCode.PARAM_ERROR)

    if not isinstance(remark, str):
        raise CustomError(ErrorCode.PARAM_ERROR)

    if flow_input:
        try:
            flow_input = orjson.loads(flow_input)
        except orjson.JSONDecodeError:
            raise CustomError(ErrorCode.PARAM_ERROR)

    approve = await Approvement.filter(callback_token=token).select_for_update().prefetch_related("approvers").first()
    if not approve:
        raise CustomError(ErrorCode.NOT_FOUND)

    if approve.status != 0:
        raise CustomError(ErrorCode.APPROVEMENT_STATUS_ERROR)

    command_approval_id = _agent_command_approval_id(approve)
    if command_approval_id:
        from services.command_center.approval_service import decide_approval

        await decide_approval(
            approve.tenant_id,
            command_approval_id,
            "approve" if agree else "reject",
            "callback",
            remark,
        )
        return response_ok("success")

    if flow_input:
        approve.params = flow_input

    approve.results = {
        **approve.results,
        token: {
            "username": "callback",
            "display_name": "外部回调",
            "agree": agree,
            "remark": remark,
            "approve_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }

    if agree:
        approve.status = 1
    else:
        approve.status = 2

    await approve.save()

    if not flow_input:
        flow_input = {}
    flow_input["approval_result"] = approve.results

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
        "tenant_id": approve.tenant_id,
    }
    await aproduce(TASK_TOPIC, msg)

    return response_ok("success")
