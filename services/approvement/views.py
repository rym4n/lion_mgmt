# coding=utf-8

from datetime import datetime
from typing import Any

import orjson
from sanic import Blueprint
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.kafka import aproduce
from models import Approvement, Runlog
from settings import TASK_TOPIC


approve_bp = Blueprint("approve", url_prefix="/approve")
__all__ = ["approve_bp"]


def _json_body(request) -> dict:
    return request.json if isinstance(request.json, dict) else {}


def _approval_executor(account_info) -> str:
    return account_info.id


def _callback_executor(approve: Approvement) -> str | None:
    return next((approver.id for approver in approve.approvers), None)


def _parse_time_range(values: Any) -> list[datetime] | None:
    if not isinstance(values, list) or len(values) != 2:
        return None
    try:
        return [
            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            for value in values
        ]
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_ERROR)


def _normalize_params(params: Any) -> list[dict]:
    if params in (None, ""):
        return []
    if not isinstance(params, list):
        raise CustomError(ErrorCode.PARAM_ERROR)
    normalized = []
    for item in params:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise CustomError(ErrorCode.PARAM_ERROR)
        value = item.get("value", item.get("default"))
        if item.get("required") is True and (
            value is None or (isinstance(value, str) and not value.strip())
        ):
            raise CustomError(ErrorCode.PARAM_ERROR)
        normalized.append({**item, "name": str(item["name"]).strip(), "value": value})
    return normalized


def _params_to_flow_input(params: Any) -> dict:
    if isinstance(params, dict):
        return dict(params)
    if not isinstance(params, list):
        return {}
    return {
        str(item["name"]): item.get("value", item.get("default"))
        for item in params
        if isinstance(item, dict) and item.get("name")
    }


def _decision_status(
    approve_type: int,
    agree: bool,
    results: dict,
    approver_ids: set[str],
) -> int:
    if not agree:
        return 2
    if approve_type == 2 and not approver_ids.issubset(results):
        return 0
    return 1


async def _approver_ids(connection, approvement_id: str) -> set[str]:
    rows = await connection.execute_query_dict(
        "SELECT account_id FROM approve_accounts WHERE approvement_id = $1",
        [approvement_id],
    )
    return {row["account_id"] for row in rows}


async def expire_pending_approvals(
    *,
    tenant_id: str | None = None,
    run_id: str | None = None,
) -> int:
    expired_condition = Q(expire_time__lt=datetime.now())
    if tenant_id:
        expired_condition &= Q(tenant_id=tenant_id)
    if run_id:
        expired_condition &= Q(run_id=run_id)

    expired_approvals = await Approvement.filter(
        expired_condition,
        status=0,
        deleted=False,
        flow_id__isnull=False,
    ).all()
    if not expired_approvals:
        return 0

    approval_ids = [approval.id for approval in expired_approvals]
    await Approvement.filter(id__in=approval_ids).update(status=3)
    for approval in expired_approvals:
        condition = Q(
            run_id=approval.run_id,
            node_id=approval.audit_node,
            node_type="approval-node",
            status=2,
            tenant_id=approval.tenant_id,
        )
        await Runlog.filter(condition).update(status=3, result="审核已超时")
    return len(expired_approvals)


@approve_bp.post("/list")
@openapi.definition(summary="查询存量剧本审批记录")
async def all_approvement(request):
    params = _json_body(request)
    try:
        page = max(1, int(params.get("page", 1)))
        size = max(1, min(int(params.get("size", 15)), 100))
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    account_id = request.ctx.session["account_info"].id
    condition = Q(
        approvers=account_id,
        tenant_id=tenant_id,
        deleted=False,
        flow_id__isnull=False,
    )
    keyword = params.get("keyword")
    statuses = params.get("status")
    if keyword:
        condition &= Q(title__icontains=keyword) | Q(content__icontains=keyword)
    if statuses:
        if not isinstance(statuses, list):
            raise CustomError(ErrorCode.PARAM_ERROR)
        condition &= Q(status__in=statuses)
    if time_range := _parse_time_range(params.get("ctime")):
        condition &= Q(ctime__range=time_range)

    await expire_pending_approvals(tenant_id=tenant_id)
    query = Approvement.filter(condition).distinct()
    total = await query.count()
    records = await query.offset((page - 1) * size).limit(size).prefetch_related(
        "approvers"
    ).all()
    return response_ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
            "data": [record.json for record in records],
        }
    )


@approve_bp.post("/todolist")
@openapi.definition(summary="查询当前用户的存量剧本审批待办")
async def todo_approvement(request):
    tenant_id = request.ctx.session.get("tenant_id")
    account_id = request.ctx.session["account_info"].id
    await expire_pending_approvals(tenant_id=tenant_id)
    records = await Approvement.filter(
        approvers=account_id,
        tenant_id=tenant_id,
        status=0,
        deleted=False,
        flow_id__isnull=False,
    ).distinct().prefetch_related("approvers").all()
    return response_ok(
        [
            record.json
            for record in records
            if account_id not in (record.results or {})
        ]
    )


@approve_bp.post("/detail")
@openapi.definition(summary="查看存量剧本审批详情")
async def approvement_detail(request):
    approvement_id = _json_body(request).get("id")
    if not approvement_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    tenant_id = request.ctx.session.get("tenant_id")
    account_id = request.ctx.session["account_info"].id
    approve = await Approvement.filter(
        id=approvement_id,
        approvers=account_id,
        tenant_id=tenant_id,
        deleted=False,
        flow_id__isnull=False,
    ).distinct().prefetch_related("approvers").first()
    if approve is None:
        raise CustomError(ErrorCode.NOT_FOUND)
    return response_ok(approve.json)


@approve_bp.post("/update")
@openapi.definition(summary="处理存量剧本审批")
async def update_approvement(request):
    param = _json_body(request)
    approvement_id = param.get("id")
    agree = param.get("agree")
    remark = param.get("remark", "")
    if not approvement_id or not isinstance(agree, bool) or not isinstance(remark, str):
        raise CustomError(ErrorCode.PARAM_ERROR)
    args = _normalize_params(param.get("params"))
    tenant_id = request.ctx.session.get("tenant_id")
    account_info = request.ctx.session["account_info"]
    msg = None

    async with in_transaction() as connection:
        approve = await Approvement.filter(
            id=approvement_id,
            tenant_id=tenant_id,
            deleted=False,
            flow_id__isnull=False,
        ).using_db(connection).select_for_update().first()
        if approve is None:
            raise CustomError(ErrorCode.NOT_FOUND)
        approver_ids = await _approver_ids(connection, approve.id)
        if account_info.id not in approver_ids:
            raise CustomError(ErrorCode.NOT_FOUND)
        if approve.status != 0 or account_info.id in (approve.results or {}):
            raise CustomError(ErrorCode.APPROVEMENT_STATUS_ERROR)

        results = dict(approve.results or {})
        results[account_info.id] = {
            "username": account_info.username,
            "display_name": account_info.display_name,
            "agree": agree,
            "remark": remark,
            "approve_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        approve.results = results
        if args:
            approve.params = args
        approve.status = _decision_status(
            approve.approve_type,
            agree,
            results,
            approver_ids,
        )
        await approve.save(
            using_db=connection,
            update_fields=["results", "params", "status", "utime"],
        )

        if approve.status in (1, 2):
            flow_input = _params_to_flow_input(approve.params)
            flow_input["approval_result"] = approve.results
            msg = {
                "run_type": "flow",
                "run_id": approve.run_id,
                "run_args": {
                    "flow_id": approve.flow_id,
                    "flow_input": flow_input,
                    "approval_node_id": approve.audit_node,
                    "approval_status": approve.status,
                    "executor": _approval_executor(account_info),
                },
                "tenant_id": tenant_id,
            }

    if msg is not None:
        await aproduce(TASK_TOPIC, msg)
    return response_ok("success")


def _callback_payload(request) -> dict:
    payload = _json_body(request) if request.method == "POST" else {}
    return {
        "token": request.args.get("token") or payload.get("token"),
        "agree": request.args.get("agree") or payload.get("agree"),
        "remark": request.args.get("remark", payload.get("remark", "")),
        "params": request.args.get("params") or payload.get("params"),
        "tenant_id": request.args.get("tenant_id") or payload.get("tenant_id"),
    }


def _parse_callback_agree(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    raise CustomError(ErrorCode.PARAM_ERROR)


@approve_bp.route("/callback", methods=["GET", "POST"])
@openapi.definition(summary="存量剧本审批外部回调")
async def update_approvement_callback(request):
    payload = _callback_payload(request)
    token = payload["token"]
    if not token or not isinstance(token, str):
        raise CustomError(ErrorCode.PARAM_ERROR)
    agree = _parse_callback_agree(payload["agree"])
    remark = payload["remark"]
    if not isinstance(remark, str):
        raise CustomError(ErrorCode.PARAM_ERROR)
    callback_params = payload["params"]
    if isinstance(callback_params, str) and callback_params:
        try:
            callback_params = orjson.loads(callback_params)
        except orjson.JSONDecodeError:
            raise CustomError(ErrorCode.PARAM_ERROR)
    if callback_params not in (None, "") and not isinstance(
        callback_params, (dict, list)
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)

    async with in_transaction() as connection:
        approve = await Approvement.filter(
            callback_token=token,
            deleted=False,
            flow_id__isnull=False,
        ).using_db(connection).select_for_update().prefetch_related(
            "approvers"
        ).first()
        if approve is None:
            raise CustomError(ErrorCode.NOT_FOUND)
        if payload["tenant_id"] is not None and payload["tenant_id"] != approve.tenant_id:
            raise CustomError(ErrorCode.NOT_FOUND)
        if approve.status != 0:
            raise CustomError(ErrorCode.APPROVEMENT_STATUS_ERROR)
        if callback_params not in (None, ""):
            approve.params = callback_params
        approve.results = {
            **(approve.results or {}),
            token: {
                "username": "callback",
                "display_name": "外部回调",
                "agree": agree,
                "remark": remark,
                "approve_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        approve.status = 1 if agree else 2
        approve.callback_token = ""
        await approve.save(
            using_db=connection,
            update_fields=["params", "results", "status", "callback_token", "utime"],
        )

        flow_input = _params_to_flow_input(approve.params)
        flow_input["approval_result"] = approve.results
        msg = {
            "run_type": "flow",
            "run_id": approve.run_id,
            "run_args": {
                "flow_id": approve.flow_id,
                "flow_input": flow_input,
                "approval_node_id": approve.audit_node,
                "approval_status": approve.status,
                "executor": _callback_executor(approve),
            },
            "tenant_id": approve.tenant_id,
        }

    await aproduce(TASK_TOPIC, msg)
    return response_ok("success")
