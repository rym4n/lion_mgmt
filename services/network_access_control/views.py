# coding=utf-8
from __future__ import annotations

from sanic import Blueprint, Request
from sanic_ext import openapi
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import check_permissions, requires_permission
from libs.utils import get_url_from_s3, upload_to_s3
from models.network_access_control import NetworkAccessPolicyCompareItem
from services.network_access_control.manager import (
    cancel_disposal_task,
    confirm_disposal_task,
    create_attachment_ref,
    create_notification_event,
    delete_actual_policy,
    delete_ledger_record,
    delete_process_record,
    discover_unknown_terminals,
    get_ledger_record_or_404,
    get_process_record_or_404,
    list_ledger_records,
    list_process_records,
    normalize_page,
    operator_name,
    policy_change_external_callback,
    retry_collection_run,
    risk_to_review_issue,
    run_collection_task,
    run_policy_compare,
    save_actual_policy,
    save_ledger_record,
    save_process_record,
    sync_actual_policies,
    tenant_id,
    transition_review_issue,
    update_compare_item_status,
)

network_access_control_bp = Blueprint(
    "network_access_control", url_prefix="/network-access-control"
)
__all__ = ["network_access_control_bp"]


def _json(request: Request) -> dict:
    return request.json or {}


def _query(request: Request) -> dict:
    return {key: request.args.get(key) for key in request.args}


def _form_data(request: Request) -> dict:
    data = {}
    for key, value in (request.form or {}).items():
        data[key] = value[0] if isinstance(value, list) and len(value) == 1 else value
    return data


def _s3_file_name(file_id: str, prefix: str) -> str:
    prefix_path = f"/files/user/{prefix}/"
    if file_id.startswith(prefix_path):
        return file_id[len(prefix_path) :]
    prefix_key = f"{prefix}/"
    if file_id.startswith(prefix_key):
        return file_id[len(prefix_key) :]
    return file_id


def _register_ledger_routes(domain: str, path: str, resource: str, title: str) -> None:
    async def list_view(request: Request, _domain=domain):
        return response_ok(
            await list_ledger_records(tenant_id(request), _domain, _json(request))
        )

    async def detail_view(request: Request, _domain=domain):
        record = await get_ledger_record_or_404(
            tenant_id(request), _domain, _json(request).get("id")
        )
        return response_ok(record.json)

    async def save_view(request: Request, _domain=domain):
        record = await save_ledger_record(
            tenant_id(request), _domain, _json(request), operator_name(request)
        )
        return response_ok(record.json)

    async def delete_view(request: Request, _domain=domain):
        return response_ok(
            await delete_ledger_record(
                tenant_id(request), _domain, _json(request).get("id")
            )
        )

    list_view.__name__ = f"{path.replace('-', '_')}_list"
    detail_view.__name__ = f"{path.replace('-', '_')}_detail"
    save_view.__name__ = f"{path.replace('-', '_')}_save"
    delete_view.__name__ = f"{path.replace('-', '_')}_delete"
    network_access_control_bp.add_route(
        requires_permission("network_access_control:r")(
            openapi.definition(summary=f"{title}列表")(list_view)
        ),
        f"/{path}/list",
        methods=["POST"],
    )
    network_access_control_bp.add_route(
        requires_permission("network_access_control:r")(
            openapi.definition(summary=f"{title}详情")(detail_view)
        ),
        f"/{path}/detail",
        methods=["POST"],
    )
    network_access_control_bp.add_route(
        requires_permission("network_access_control:c", "network_access_control:u")(
            openapi.definition(summary=f"保存{title}")(save_view)
        ),
        f"/{path}/save",
        methods=["POST"],
    )
    network_access_control_bp.add_route(
        requires_permission("network_access_control:d")(
            openapi.definition(summary=f"删除{title}")(delete_view)
        ),
        f"/{path}/delete",
        methods=["POST"],
    )


_register_ledger_routes(
    "personnel-authorization",
    "personnel-authorization",
    "authorization",
    "项目授权台账",
)
_register_ledger_routes("nda-record", "nda-record", "nda", "NDA台账")
_register_ledger_routes("unknown-terminal", "unknown-terminal", "terminal", "未知终端")
_register_ledger_routes("boundary-log", "boundary-log", "boundary", "边界安全日志")
_register_ledger_routes(
    "exception-device-log", "exception-device-log", "exception", "例外设备日志"
)


def _register_process_routes(
    domain: str,
    path: str,
    resource: str,
    title: str,
    blueprint: Blueprint = network_access_control_bp,
    *,
    readonly: bool = False,
) -> None:
    async def list_view(request: Request, _domain=domain):
        return response_ok(
            await list_process_records(tenant_id(request), _domain, _json(request))
        )

    async def detail_view(request: Request, _domain=domain):
        record = await get_process_record_or_404(
            tenant_id(request), _domain, _json(request).get("id")
        )
        return response_ok(record.json)

    async def save_view(request: Request, _domain=domain):
        data = _json(request)
        await check_permissions(
            request,
            f"network_access_control:{'u' if data.get('id') else 'c'}",
        )
        if _domain == "actual-policy":
            record = await save_actual_policy(
                tenant_id(request), data, operator_name(request)
            )
        else:
            record = await save_process_record(
                tenant_id(request), _domain, data, operator_name(request)
            )
        return response_ok(record.json)

    async def delete_view(request: Request, _domain=domain):
        if _domain == "actual-policy":
            return response_ok(
                await delete_actual_policy(
                    tenant_id(request),
                    _json(request).get("id"),
                    operator_name(request),
                )
            )
        return response_ok(
            await delete_process_record(
                tenant_id(request), _domain, _json(request).get("id")
            )
        )

    list_view.__name__ = f"{path.replace('-', '_')}_list"
    detail_view.__name__ = f"{path.replace('-', '_')}_detail"
    save_view.__name__ = f"{path.replace('-', '_')}_save"
    delete_view.__name__ = f"{path.replace('-', '_')}_delete"
    blueprint.add_route(
        requires_permission("network_access_control:r")(
            openapi.definition(summary=f"{title}列表")(list_view)
        ),
        f"/{path}/list",
        methods=["POST"],
    )
    blueprint.add_route(
        requires_permission("network_access_control:r")(
            openapi.definition(summary=f"{title}详情")(detail_view)
        ),
        f"/{path}/detail",
        methods=["POST"],
    )
    if not readonly:
        blueprint.add_route(
            requires_permission("network_access_control:c", "network_access_control:u")(
                openapi.definition(summary=f"保存{title}")(save_view)
            ),
            f"/{path}/save",
            methods=["POST"],
        )
        blueprint.add_route(
            requires_permission("network_access_control:d")(
                openapi.definition(summary=f"删除{title}")(delete_view)
            ),
            f"/{path}/delete",
            methods=["POST"],
        )


_register_process_routes("collection-task", "collection-task", "collection", "采集任务")
_register_process_routes("collection-run", "collection-run", "collection", "采集运行")
_register_process_routes(
    "parse-template", "parse-template", "collection", "解析映射模板"
)
_register_process_routes("preset-policy", "preset-policy", "policy", "预设策略")
_register_process_routes(
    "actual-policy", "actual-policy", "policy", "策略清单", network_access_control_bp
)
_register_process_routes(
    "policy-change",
    "policy-change",
    "policy",
    "策略更新历史",
    network_access_control_bp,
    readonly=True,
)
_register_process_routes("network-topology", "network-topology", "topology", "网络拓扑")
_register_process_routes("review-issue", "review-issue", "issue", "审查问题")
_register_process_routes(
    "remediation-record", "remediation-record", "issue", "整改记录"
)
_register_process_routes("disposal-rule", "disposal-rule", "disposal", "处置规则")
_register_process_routes("disposal-task", "disposal-task", "disposal", "处置任务")
_register_process_routes("attachment-ref", "attachment-ref", "attachment", "附件引用")
_register_process_routes(
    "notification-event", "notification-event", "notify", "通知事件"
)


@network_access_control_bp.post("/collection-task/run")
@openapi.definition(summary="运行采集任务")
@requires_permission("network_access_control:u")
async def collection_task_run(request: Request):
    return response_ok(
        await run_collection_task(
            tenant_id(request),
            _json(request).get("id") or _json(request).get("task_id"),
            operator_name(request),
        )
    )


@network_access_control_bp.post("/collection-run/retry")
@openapi.definition(summary="重试采集运行")
@requires_permission("network_access_control:u")
async def collection_run_retry(request: Request):
    return response_ok(
        await retry_collection_run(
            tenant_id(request), _json(request).get("id"), operator_name(request)
        )
    )


@network_access_control_bp.post("/actual-policy/sync")
@openapi.definition(summary="批量同步访问控制策略并记录历史")
@requires_permission("network_access_control:c", "network_access_control:u")
async def actual_policy_sync(request: Request):
    await check_permissions(request, "network_access_control:c")
    await check_permissions(request, "network_access_control:u")
    if _json(request).get("replace") is True:
        await check_permissions(request, "network_access_control:d")
    return response_ok(
        await sync_actual_policies(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@network_access_control_bp.post("/policy-compare/run")
@openapi.definition(summary="运行策略比对")
@requires_permission("network_access_control:u")
async def policy_compare_run(request: Request):
    return response_ok(
        await run_policy_compare(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@network_access_control_bp.post("/policy-compare/run-list")
@openapi.definition(summary="策略比对运行列表")
@requires_permission("network_access_control:r")
async def policy_compare_run_list(request: Request):
    return response_ok(
        await list_process_records(
            tenant_id(request), "policy-compare-run", _json(request)
        )
    )


@network_access_control_bp.post("/policy-compare/item-list")
@openapi.definition(summary="策略比对明细列表")
@requires_permission("network_access_control:r")
async def policy_compare_item_list(request: Request):
    data = _json(request)
    page, size, offset = normalize_page(data)
    condition = Q(tenant_id=tenant_id(request), deleted=False)
    for field in ["compare_run_id", "compare_type", "handle_status"]:
        if data.get(field):
            condition &= Q(**{field: data[field]})
    queryset = NetworkAccessPolicyCompareItem.filter(condition)
    total = await queryset.count()
    items = await queryset.order_by("-utime").offset(offset).limit(size).all()
    return response_ok(
        {
            "total": total,
            "items": [item.json for item in items],
            "page": page,
            "size": size,
        }
    )


@network_access_control_bp.post("/policy-compare/item-confirm")
@openapi.definition(summary="确认策略比对明细")
@requires_permission("network_access_control:u")
async def policy_compare_item_confirm(request: Request):
    return response_ok(
        await update_compare_item_status(
            tenant_id(request),
            _json(request).get("id"),
            "confirmed",
            operator_name(request),
        )
    )


@network_access_control_bp.post("/policy-compare/item-ignore")
@openapi.definition(summary="忽略策略比对明细")
@requires_permission("network_access_control:u")
async def policy_compare_item_ignore(request: Request):
    return response_ok(
        await update_compare_item_status(
            tenant_id(request),
            _json(request).get("id"),
            "ignored",
            operator_name(request),
        )
    )


@network_access_control_bp.post("/policy-change/external-callback")
@openapi.definition(summary="策略变更外部审批结果回写")
@requires_permission("network_access_control:u")
async def policy_change_callback(request: Request):
    return response_ok(
        await policy_change_external_callback(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@network_access_control_bp.post("/unknown-terminal/discover")
@openapi.definition(summary="网络侧未知终端发现")
@requires_permission("network_access_control:c")
async def unknown_terminal_discover(request: Request):
    return response_ok(
        await discover_unknown_terminals(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@network_access_control_bp.post("/review-issue/transition")
@openapi.definition(summary="审查问题状态流转")
@requires_permission("network_access_control:u")
async def review_issue_transition(request: Request):
    return response_ok(
        await transition_review_issue(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@network_access_control_bp.post("/review-issue/from-risk")
@openapi.definition(summary="风险转审查问题")
@requires_permission("network_access_control:c")
async def review_issue_from_risk(request: Request):
    return response_ok(
        await risk_to_review_issue(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@network_access_control_bp.post("/disposal-task/confirm")
@openapi.definition(summary="确认执行处置任务")
@requires_permission("network_access_control:u")
async def disposal_task_confirm(request: Request):
    return response_ok(
        await confirm_disposal_task(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@network_access_control_bp.post("/disposal-task/retry")
@openapi.definition(summary="重试处置任务")
@requires_permission("network_access_control:u")
async def disposal_task_retry(request: Request):
    return response_ok(
        await confirm_disposal_task(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@network_access_control_bp.post("/disposal-task/cancel")
@openapi.definition(summary="取消处置任务")
@requires_permission("network_access_control:u")
async def disposal_task_cancel(request: Request):
    return response_ok(
        await cancel_disposal_task(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@network_access_control_bp.post("/notification-event/generate")
@openapi.definition(summary="生成通知事件负载")
@requires_permission("network_access_control:c")
async def notification_event_generate(request: Request):
    return response_ok(
        await create_notification_event(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@network_access_control_bp.post("/attachment-ref/upload")
@openapi.definition(summary="上传网络访问控制附件")
@requires_permission("network_access_control:c")
async def attachment_ref_upload(request: Request):
    data = _json(request) or _form_data(request)
    file = request.files.get("file") if request.files else None
    if not file:
        raise CustomError(ErrorCode.PARAM_ERROR)
    file_id = await upload_to_s3(
        file.body,
        "user",
        file.name,
        "network_access_control",
        getattr(file, "type", None),
    )
    payload = {
        **data,
        "file_id": file_id,
        "file_name": file.name,
        "file_type": getattr(file, "type", None),
        "file_size": len(file.body or b""),
    }
    return response_ok(
        await create_attachment_ref(tenant_id(request), payload, operator_name(request))
    )


@network_access_control_bp.post("/attachment-ref/download-url")
@openapi.definition(summary="获取网络访问控制附件下载链接")
@requires_permission("network_access_control:r")
async def attachment_ref_download_url(request: Request):
    record = await get_process_record_or_404(
        tenant_id(request), "attachment-ref", _json(request).get("id")
    )
    url = await get_url_from_s3(
        "user",
        _s3_file_name(record.file_id, "network_access_control"),
        request.host,
        "network_access_control",
    )
    return response_ok(
        {"url": url, "file_id": record.file_id, "file_name": record.file_name}
    )
