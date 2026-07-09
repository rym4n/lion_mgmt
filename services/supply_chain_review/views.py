# coding=utf-8
from __future__ import annotations

from sanic import Blueprint, Request
from sanic_ext import openapi
from tortoise.expressions import Q

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from libs.utils import get_url_from_s3, upload_to_s3
from models.supply_chain_review import (
    SupplyChainCi,
    SupplyChainCiFieldDef,
    SupplyChainCiRelation,
    SupplyChainCiRelationType,
    SupplyChainCiType,
    SupplyChainPolicyCompareItem,
)
from services.supply_chain_review.manager import (
    build_relation_graph,
    build_project_tree,
    cancel_disposal_task,
    confirm_disposal_task,
    create_attachment_ref,
    create_notification_event,
    delete_ledger_record,
    delete_process_record,
    discover_unknown_terminals,
    export_model_package,
    fields_for_type,
    get_ci_or_404,
    get_ci_type_or_404,
    get_ledger_record_or_404,
    get_process_record_or_404,
    get_relation_type_or_404,
    import_model_package,
    list_ledger_records,
    list_process_records,
    normalize_page,
    operator_name,
    overview_summary,
    overview_todo,
    policy_change_external_callback,
    retry_collection_run,
    risk_to_review_issue,
    run_collection_task,
    run_policy_compare,
    save_ci,
    save_ci_type,
    save_field,
    save_ledger_record,
    save_process_record,
    save_review_issue,
    save_relation,
    save_relation_type,
    tenant_id,
    update_compare_item_status,
    transition_review_issue,
)

supply_chain_review_cmdb_bp = Blueprint("supply_chain_review_cmdb", url_prefix="/supply-chain-review")
supply_chain_review_ledger_bp = Blueprint("supply_chain_review_ledger", url_prefix="/supply-chain-review")
supply_chain_review_process_bp = Blueprint("supply_chain_review_process", url_prefix="/supply-chain-review")
supply_chain_review_action_bp = Blueprint("supply_chain_review_action", url_prefix="/supply-chain-review")
supply_chain_review_attachment_bp = Blueprint("supply_chain_review_attachment", url_prefix="/supply-chain-review")
network_access_control_bp = Blueprint("network_access_control", url_prefix="/supply-chain-review")

SUPPLY_CHAIN_REVIEW_BLUEPRINTS = (
    supply_chain_review_cmdb_bp,
    supply_chain_review_ledger_bp,
    supply_chain_review_process_bp,
    network_access_control_bp,
    supply_chain_review_action_bp,
    supply_chain_review_attachment_bp,
)
ASSET_MANAGEMENT_BLUEPRINTS = (supply_chain_review_cmdb_bp,)
NETWORK_ACCESS_CONTROL_BLUEPRINTS = (network_access_control_bp,)

# Backward-compatible module handle for existing license/menu metadata.
supply_chain_review_bp = SUPPLY_CHAIN_REVIEW_BLUEPRINTS
asset_management_bp = ASSET_MANAGEMENT_BLUEPRINTS
network_access_control_module_bp = NETWORK_ACCESS_CONTROL_BLUEPRINTS

__all__ = [
    "SUPPLY_CHAIN_REVIEW_BLUEPRINTS",
    "ASSET_MANAGEMENT_BLUEPRINTS",
    "NETWORK_ACCESS_CONTROL_BLUEPRINTS",
    "supply_chain_review_bp",
    "asset_management_bp",
    "network_access_control_module_bp",
    "supply_chain_review_cmdb_bp",
    "supply_chain_review_ledger_bp",
    "supply_chain_review_process_bp",
    "supply_chain_review_action_bp",
    "supply_chain_review_attachment_bp",
    "network_access_control_bp",
]


def _json(request: Request) -> dict:
    return request.json or {}


def _form_data(request: Request) -> dict:
    data = {}
    for key, value in (request.form or {}).items():
        data[key] = value[0] if isinstance(value, list) and len(value) == 1 else value
    return data


def _s3_file_name(file_id: str, prefix: str) -> str:
    prefix_path = f"/files/user/{prefix}/"
    if file_id.startswith(prefix_path):
        return file_id[len(prefix_path):]
    prefix_key = f"{prefix}/"
    if file_id.startswith(prefix_key):
        return file_id[len(prefix_key):]
    return file_id


def _keyword_condition(keyword: str | None):
    if not keyword:
        return Q()
    return Q(name__icontains=keyword) | Q(key__icontains=keyword)


@supply_chain_review_cmdb_bp.post("/ci-type/list")
@openapi.definition(summary="CMDB模型列表")
@requires_permission("supply_chain_review:model:r")
async def ci_type_list(request: Request):
    data = _json(request)
    page, size, offset = normalize_page(data)
    condition = Q(tenant_id=tenant_id(request), deleted=False) & _keyword_condition(data.get("keyword"))
    if data.get("status"):
        condition &= Q(status=data["status"])
    queryset = SupplyChainCiType.filter(condition)
    total = await queryset.count()
    items = await queryset.order_by("key").offset(offset).limit(size).all()
    return response_ok({"total": total, "items": [item.json for item in items], "page": page, "size": size})


@supply_chain_review_cmdb_bp.post("/ci-type/detail")
@openapi.definition(summary="CMDB模型详情")
@requires_permission("supply_chain_review:model:r")
async def ci_type_detail(request: Request):
    data = _json(request)
    ci_type = await get_ci_type_or_404(tenant_id(request), data.get("id") or data.get("key"))
    fields = await fields_for_type(ci_type.tenant_id, ci_type.key)
    payload = ci_type.json
    payload["fields"] = [field.json for field in fields]
    return response_ok(payload)


@supply_chain_review_cmdb_bp.post("/ci-type/save")
@openapi.definition(summary="保存CMDB模型")
@requires_permission("supply_chain_review:model:c", "supply_chain_review:model:u")
async def ci_type_save(request: Request):
    item = await save_ci_type(tenant_id(request), _json(request), operator_name(request))
    return response_ok(item.json)


@supply_chain_review_cmdb_bp.post("/ci-type/delete")
@openapi.definition(summary="删除CMDB模型")
@requires_permission("supply_chain_review:model:d")
async def ci_type_delete(request: Request):
    ci_type = await get_ci_type_or_404(tenant_id(request), _json(request).get("id") or _json(request).get("key"))
    if ci_type.is_locked:
        raise CustomError(ErrorCode.FORBIDDEN)
    ci_type.deleted = True
    await ci_type.save()
    await SupplyChainCiFieldDef.filter(tenant_id=ci_type.tenant_id, ci_type_key=ci_type.key, deleted=False).update(deleted=True)
    return response_ok({"id": ci_type.id})


@supply_chain_review_cmdb_bp.post("/ci-type/export")
@openapi.definition(summary="导出CMDB模型包")
@requires_permission("supply_chain_review:model:r")
async def ci_type_export(request: Request):
    data = _json(request)
    return response_ok(
        await export_model_package(
            tenant_id(request),
            data.get("ci_type_keys"),
            bool(data.get("include_related", False)),
        )
    )


@supply_chain_review_cmdb_bp.post("/ci-type/import")
@openapi.definition(summary="导入CMDB模型包")
@requires_permission("supply_chain_review:model:c")
async def ci_type_import(request: Request):
    data = _json(request)
    package = data.get("package") or data
    if not isinstance(package, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return response_ok(await import_model_package(tenant_id(request), package, operator_name(request)))


@supply_chain_review_cmdb_bp.post("/ci-field/list")
@openapi.definition(summary="CMDB字段列表")
@requires_permission("supply_chain_review:field:r")
async def ci_field_list(request: Request):
    data = _json(request)
    ci_type_key = data.get("ci_type_key")
    if not ci_type_key:
        raise CustomError(ErrorCode.PARAM_ERROR)
    await get_ci_type_or_404(tenant_id(request), ci_type_key)
    fields = await fields_for_type(tenant_id(request), ci_type_key)
    return response_ok({"items": [item.json for item in fields], "total": len(fields)})


@supply_chain_review_cmdb_bp.post("/ci-field/save")
@openapi.definition(summary="保存CMDB字段")
@requires_permission("supply_chain_review:field:c", "supply_chain_review:field:u")
async def ci_field_save(request: Request):
    item = await save_field(tenant_id(request), _json(request), operator_name(request))
    return response_ok(item.json)


@supply_chain_review_cmdb_bp.post("/ci-field/delete")
@openapi.definition(summary="删除CMDB字段")
@requires_permission("supply_chain_review:field:d")
async def ci_field_delete(request: Request):
    data = _json(request)
    field = await SupplyChainCiFieldDef.filter(id=data.get("id"), tenant_id=tenant_id(request), deleted=False).first()
    if not field:
        raise CustomError(ErrorCode.NOT_FOUND)
    ci_type = await get_ci_type_or_404(tenant_id(request), field.ci_type_key)
    if ci_type.is_locked and field.is_locked:
        raise CustomError(ErrorCode.FORBIDDEN)
    field.deleted = True
    await field.save()
    return response_ok({"id": field.id})


@supply_chain_review_cmdb_bp.post("/relation-type/list")
@openapi.definition(summary="CMDB关系类型列表")
@requires_permission("supply_chain_review:relation:r")
async def relation_type_list(request: Request):
    data = _json(request)
    page, size, offset = normalize_page(data)
    condition = Q(tenant_id=tenant_id(request), deleted=False) & _keyword_condition(data.get("keyword"))
    queryset = SupplyChainCiRelationType.filter(condition)
    total = await queryset.count()
    items = await queryset.order_by("key").offset(offset).limit(size).all()
    return response_ok({"total": total, "items": [item.json for item in items], "page": page, "size": size})


@supply_chain_review_cmdb_bp.post("/relation-type/detail")
@openapi.definition(summary="CMDB关系类型详情")
@requires_permission("supply_chain_review:relation:r")
async def relation_type_detail(request: Request):
    data = _json(request)
    relation_type = await get_relation_type_or_404(tenant_id(request), data.get("id") or data.get("key"))
    return response_ok(relation_type.json)


@supply_chain_review_cmdb_bp.post("/relation-type/save")
@openapi.definition(summary="保存CMDB关系类型")
@requires_permission("supply_chain_review:relation:c", "supply_chain_review:relation:u")
async def relation_type_save(request: Request):
    item = await save_relation_type(tenant_id(request), _json(request), operator_name(request))
    return response_ok(item.json)


@supply_chain_review_cmdb_bp.post("/relation-type/delete")
@openapi.definition(summary="删除CMDB关系类型")
@requires_permission("supply_chain_review:relation:d")
async def relation_type_delete(request: Request):
    relation_type = await get_relation_type_or_404(tenant_id(request), _json(request).get("id") or _json(request).get("key"))
    if relation_type.is_locked:
        raise CustomError(ErrorCode.FORBIDDEN)
    relation_type.deleted = True
    await relation_type.save()
    return response_ok({"id": relation_type.id})


@supply_chain_review_cmdb_bp.post("/ci/list")
@openapi.definition(summary="CI实例列表")
@requires_permission("supply_chain_review:ci:r")
async def ci_list(request: Request):
    data = _json(request)
    page, size, offset = normalize_page(data)
    condition = Q(tenant_id=tenant_id(request), deleted=False)
    for field in ["ci_type_key", "project_area_id", "accessory_project_id", "status", "risk_status"]:
        if data.get(field):
            condition &= Q(**{field: data[field]})
    if data.get("keyword"):
        condition &= Q(name__icontains=data["keyword"])
    queryset = SupplyChainCi.filter(condition)
    total = await queryset.count()
    items = await queryset.order_by("-utime").offset(offset).limit(size).all()
    return response_ok({"total": total, "items": [item.json for item in items], "page": page, "size": size})


@supply_chain_review_cmdb_bp.post("/ci/detail")
@openapi.definition(summary="CI实例详情")
@requires_permission("supply_chain_review:ci:r")
async def ci_detail(request: Request):
    ci = await get_ci_or_404(tenant_id(request), _json(request).get("id") or _json(request).get("ci_id"))
    fields = await fields_for_type(ci.tenant_id, ci.ci_type_key)
    payload = ci.json
    payload["fields"] = [item.json for item in fields]
    return response_ok(payload)


@supply_chain_review_cmdb_bp.post("/ci/save")
@openapi.definition(summary="保存CI实例")
@requires_permission("supply_chain_review:ci:c", "supply_chain_review:ci:u")
async def ci_save(request: Request):
    item = await save_ci(tenant_id(request), _json(request), operator_name(request))
    return response_ok(item.json)


@supply_chain_review_cmdb_bp.post("/ci/delete")
@openapi.definition(summary="删除CI实例")
@requires_permission("supply_chain_review:ci:d")
async def ci_delete(request: Request):
    ci = await get_ci_or_404(tenant_id(request), _json(request).get("id") or _json(request).get("ci_id"))
    ci.deleted = True
    await ci.save()
    await SupplyChainCiRelation.filter(Q(source_ci_id=ci.id) | Q(target_ci_id=ci.id), tenant_id=ci.tenant_id, deleted=False).update(deleted=True)
    return response_ok({"id": ci.id})


@supply_chain_review_cmdb_bp.post("/ci/import")
@openapi.definition(summary="批量导入CI实例")
@requires_permission("supply_chain_review:ci:c")
async def ci_import(request: Request):
    data = _json(request)
    records = data.get("items") or data.get("records") or []
    if not isinstance(records, list):
        raise CustomError(ErrorCode.PARAM_ERROR)
    imported = []
    for record in records:
        record = {**record, "ci_type_key": record.get("ci_type_key") or data.get("ci_type_key")}
        imported.append((await save_ci(tenant_id(request), record, operator_name(request))).json)
    return response_ok({"total": len(imported), "items": imported})


@supply_chain_review_cmdb_bp.post("/ci/import-template")
@openapi.definition(summary="获取CI导入模板")
@requires_permission("supply_chain_review:ci:r")
async def ci_import_template(request: Request):
    data = _json(request)
    ci_type = await get_ci_type_or_404(tenant_id(request), data.get("ci_type_key"))
    fields = await fields_for_type(ci_type.tenant_id, ci_type.key)
    columns = [
        {"field_key": "name", "label": "名称", "required": True, "data_type": "text"},
        {"field_key": "project_area_id", "label": "厂区CI ID", "required": False, "data_type": "text"},
        {"field_key": "accessory_project_id", "label": "配件项目CI ID", "required": False, "data_type": "text"},
    ]
    columns.extend([field.json for field in fields])
    return response_ok({"ci_type": ci_type.json, "columns": columns})


@supply_chain_review_cmdb_bp.post("/relation/list")
@openapi.definition(summary="CI关系列表")
@requires_permission("supply_chain_review:relation:r")
async def relation_list(request: Request):
    data = _json(request)
    page, size, offset = normalize_page(data)
    condition = Q(tenant_id=tenant_id(request), deleted=False)
    for field in ["relation_type_key", "source_ci_id", "target_ci_id"]:
        if data.get(field):
            condition &= Q(**{field: data[field]})
    queryset = SupplyChainCiRelation.filter(condition)
    total = await queryset.count()
    items = await queryset.order_by("-utime").offset(offset).limit(size).all()
    return response_ok({"total": total, "items": [item.json for item in items], "page": page, "size": size})


@supply_chain_review_cmdb_bp.post("/relation/detail")
@openapi.definition(summary="CI关系详情")
@requires_permission("supply_chain_review:relation:r")
async def relation_detail(request: Request):
    relation = await SupplyChainCiRelation.filter(id=_json(request).get("id"), tenant_id=tenant_id(request), deleted=False).first()
    if not relation:
        raise CustomError(ErrorCode.NOT_FOUND)
    return response_ok(relation.json)


@supply_chain_review_cmdb_bp.post("/relation/save")
@openapi.definition(summary="保存CI关系")
@requires_permission("supply_chain_review:relation:c", "supply_chain_review:relation:u")
async def relation_save(request: Request):
    item = await save_relation(tenant_id(request), _json(request), operator_name(request))
    return response_ok(item.json)


@supply_chain_review_cmdb_bp.post("/relation/delete")
@openapi.definition(summary="删除CI关系")
@requires_permission("supply_chain_review:relation:d")
async def relation_delete(request: Request):
    relation = await SupplyChainCiRelation.filter(id=_json(request).get("id"), tenant_id=tenant_id(request), deleted=False).first()
    if not relation:
        raise CustomError(ErrorCode.NOT_FOUND)
    relation.deleted = True
    await relation.save()
    return response_ok({"id": relation.id})


@supply_chain_review_cmdb_bp.post("/relation/graph")
@openapi.definition(summary="CI关系拓扑")
@requires_permission("supply_chain_review:relation:r")
async def relation_graph(request: Request):
    data = _json(request)
    return response_ok(await build_relation_graph(tenant_id(request), data.get("ci_id"), data.get("depth", 1)))


@supply_chain_review_cmdb_bp.post("/relation/import")
@openapi.definition(summary="批量导入CI关系")
@requires_permission("supply_chain_review:relation:c")
async def relation_import(request: Request):
    data = _json(request)
    records = data.get("items") or data.get("records") or []
    if not isinstance(records, list):
        raise CustomError(ErrorCode.PARAM_ERROR)
    imported = []
    for record in records:
        imported.append((await save_relation(tenant_id(request), record, operator_name(request))).json)
    return response_ok({"total": len(imported), "items": imported})


@supply_chain_review_cmdb_bp.post("/project-view/tree")
@openapi.definition(summary="安全管理项目视图树")
@requires_permission("supply_chain_review:project:r")
async def project_view_tree(request: Request):
    return response_ok(await build_project_tree(tenant_id(request)))


@supply_chain_review_cmdb_bp.post("/overview/summary")
@openapi.definition(summary="安全管理概览统计")
@requires_permission("supply_chain_review:overview:r")
async def overview_summary_view(request: Request):
    return response_ok(await overview_summary(tenant_id(request), _json(request)))


@supply_chain_review_cmdb_bp.post("/overview/todo")
@openapi.definition(summary="安全管理待办统计")
@requires_permission("supply_chain_review:overview:r")
async def overview_todo_view(request: Request):
    return response_ok(await overview_todo(tenant_id(request), _json(request)))


def _register_ledger_routes(domain: str, path: str, resource: str, title: str) -> None:
    async def list_view(request: Request, _domain=domain):
        return response_ok(await list_ledger_records(tenant_id(request), _domain, _json(request)))

    async def detail_view(request: Request, _domain=domain):
        record = await get_ledger_record_or_404(tenant_id(request), _domain, _json(request).get("id"))
        return response_ok(record.json)

    async def save_view(request: Request, _domain=domain):
        record = await save_ledger_record(tenant_id(request), _domain, _json(request), operator_name(request))
        return response_ok(record.json)

    async def delete_view(request: Request, _domain=domain):
        return response_ok(await delete_ledger_record(tenant_id(request), _domain, _json(request).get("id")))

    list_view.__name__ = f"{path.replace('-', '_')}_list"
    detail_view.__name__ = f"{path.replace('-', '_')}_detail"
    save_view.__name__ = f"{path.replace('-', '_')}_save"
    delete_view.__name__ = f"{path.replace('-', '_')}_delete"
    supply_chain_review_ledger_bp.add_route(
        requires_permission(f"supply_chain_review:{resource}:r")(openapi.definition(summary=f"{title}列表")(list_view)),
        f"/{path}/list",
        methods=["POST"],
    )
    supply_chain_review_ledger_bp.add_route(
        requires_permission(f"supply_chain_review:{resource}:r")(openapi.definition(summary=f"{title}详情")(detail_view)),
        f"/{path}/detail",
        methods=["POST"],
    )
    supply_chain_review_ledger_bp.add_route(
        requires_permission(f"supply_chain_review:{resource}:c", f"supply_chain_review:{resource}:u")(openapi.definition(summary=f"保存{title}")(save_view)),
        f"/{path}/save",
        methods=["POST"],
    )
    supply_chain_review_ledger_bp.add_route(
        requires_permission(f"supply_chain_review:{resource}:d")(openapi.definition(summary=f"删除{title}")(delete_view)),
        f"/{path}/delete",
        methods=["POST"],
    )


_register_ledger_routes("personnel-authorization", "personnel-authorization", "authorization", "项目授权台账")
_register_ledger_routes("nda-record", "nda-record", "nda", "NDA台账")
_register_ledger_routes("unknown-terminal", "unknown-terminal", "terminal", "未知终端")
_register_ledger_routes("boundary-log", "boundary-log", "boundary", "边界安全日志")
_register_ledger_routes("exception-device-log", "exception-device-log", "exception", "例外设备日志")


def _register_process_routes(
    domain: str,
    path: str,
    resource: str,
    title: str,
    blueprint: Blueprint = supply_chain_review_process_bp,
) -> None:
    async def list_view(request: Request, _domain=domain):
        return response_ok(await list_process_records(tenant_id(request), _domain, _json(request)))

    async def detail_view(request: Request, _domain=domain):
        record = await get_process_record_or_404(tenant_id(request), _domain, _json(request).get("id"))
        return response_ok(record.json)

    async def save_view(request: Request, _domain=domain):
        record = await save_process_record(tenant_id(request), _domain, _json(request), operator_name(request))
        return response_ok(record.json)

    async def delete_view(request: Request, _domain=domain):
        return response_ok(await delete_process_record(tenant_id(request), _domain, _json(request).get("id")))

    list_view.__name__ = f"{path.replace('-', '_')}_list"
    detail_view.__name__ = f"{path.replace('-', '_')}_detail"
    save_view.__name__ = f"{path.replace('-', '_')}_save"
    delete_view.__name__ = f"{path.replace('-', '_')}_delete"
    blueprint.add_route(
        requires_permission(f"supply_chain_review:{resource}:r")(openapi.definition(summary=f"{title}列表")(list_view)),
        f"/{path}/list",
        methods=["POST"],
    )
    blueprint.add_route(
        requires_permission(f"supply_chain_review:{resource}:r")(openapi.definition(summary=f"{title}详情")(detail_view)),
        f"/{path}/detail",
        methods=["POST"],
    )
    blueprint.add_route(
        requires_permission(f"supply_chain_review:{resource}:c", f"supply_chain_review:{resource}:u")(openapi.definition(summary=f"保存{title}")(save_view)),
        f"/{path}/save",
        methods=["POST"],
    )
    blueprint.add_route(
        requires_permission(f"supply_chain_review:{resource}:d")(openapi.definition(summary=f"删除{title}")(delete_view)),
        f"/{path}/delete",
        methods=["POST"],
    )


_register_process_routes("collection-task", "collection-task", "collection", "采集任务")
_register_process_routes("collection-run", "collection-run", "collection", "采集运行")
_register_process_routes("parse-template", "parse-template", "collection", "解析映射模板")
_register_process_routes("preset-policy", "preset-policy", "policy", "预设策略", network_access_control_bp)
_register_process_routes("actual-policy", "actual-policy", "policy", "实际策略", network_access_control_bp)
_register_process_routes("policy-change", "policy-change", "policy", "策略变更", network_access_control_bp)
_register_process_routes("network-topology", "network-topology", "topology", "网络拓扑")
_register_process_routes("review-issue", "review-issue", "issue", "审查问题")
_register_process_routes("remediation-record", "remediation-record", "issue", "整改记录")
_register_process_routes("disposal-rule", "disposal-rule", "disposal", "处置规则")
_register_process_routes("disposal-task", "disposal-task", "disposal", "处置任务")
_register_process_routes("attachment-ref", "attachment-ref", "attachment", "附件引用")
_register_process_routes("notification-event", "notification-event", "notify", "通知事件")


@supply_chain_review_action_bp.post("/collection-task/run")
@openapi.definition(summary="运行采集任务")
@requires_permission("supply_chain_review:collection:u")
async def collection_task_run(request: Request):
    return response_ok(await run_collection_task(tenant_id(request), _json(request).get("id") or _json(request).get("task_id"), operator_name(request)))


@supply_chain_review_action_bp.post("/collection-run/retry")
@openapi.definition(summary="重试采集运行")
@requires_permission("supply_chain_review:collection:u")
async def collection_run_retry(request: Request):
    return response_ok(await retry_collection_run(tenant_id(request), _json(request).get("id"), operator_name(request)))


@network_access_control_bp.post("/policy-compare/run")
@openapi.definition(summary="运行策略比对")
@requires_permission("supply_chain_review:policy:u")
async def policy_compare_run(request: Request):
    return response_ok(await run_policy_compare(tenant_id(request), _json(request), operator_name(request)))


@network_access_control_bp.post("/policy-compare/run-list")
@openapi.definition(summary="策略比对运行列表")
@requires_permission("supply_chain_review:policy:r")
async def policy_compare_run_list(request: Request):
    return response_ok(await list_process_records(tenant_id(request), "policy-compare-run", _json(request)))


@network_access_control_bp.post("/policy-compare/item-list")
@openapi.definition(summary="策略比对明细列表")
@requires_permission("supply_chain_review:policy:r")
async def policy_compare_item_list(request: Request):
    data = _json(request)
    page, size, offset = normalize_page(data)
    condition = Q(tenant_id=tenant_id(request), deleted=False)
    for field in ["compare_run_id", "compare_type", "handle_status"]:
        if data.get(field):
            condition &= Q(**{field: data[field]})
    queryset = SupplyChainPolicyCompareItem.filter(condition)
    total = await queryset.count()
    items = await queryset.order_by("-utime").offset(offset).limit(size).all()
    return response_ok({"total": total, "items": [item.json for item in items], "page": page, "size": size})


@network_access_control_bp.post("/policy-compare/item-confirm")
@openapi.definition(summary="确认策略比对明细")
@requires_permission("supply_chain_review:policy:u")
async def policy_compare_item_confirm(request: Request):
    return response_ok(await update_compare_item_status(tenant_id(request), _json(request).get("id"), "confirmed", operator_name(request)))


@network_access_control_bp.post("/policy-compare/item-ignore")
@openapi.definition(summary="忽略策略比对明细")
@requires_permission("supply_chain_review:policy:u")
async def policy_compare_item_ignore(request: Request):
    return response_ok(await update_compare_item_status(tenant_id(request), _json(request).get("id"), "ignored", operator_name(request)))


@network_access_control_bp.post("/policy-change/external-callback")
@openapi.definition(summary="策略变更外部审批结果回写")
@requires_permission("supply_chain_review:policy:u")
async def policy_change_callback(request: Request):
    return response_ok(await policy_change_external_callback(tenant_id(request), _json(request), operator_name(request)))


@supply_chain_review_action_bp.post("/unknown-terminal/discover")
@openapi.definition(summary="网络侧未知终端发现")
@requires_permission("supply_chain_review:terminal:c")
async def unknown_terminal_discover(request: Request):
    return response_ok(await discover_unknown_terminals(tenant_id(request), _json(request), operator_name(request)))


@supply_chain_review_action_bp.post("/review-issue/transition")
@openapi.definition(summary="审查问题状态流转")
@requires_permission("supply_chain_review:issue:u")
async def review_issue_transition(request: Request):
    return response_ok(await transition_review_issue(tenant_id(request), _json(request), operator_name(request)))


@supply_chain_review_action_bp.post("/review-issue/from-risk")
@openapi.definition(summary="风险转审查问题")
@requires_permission("supply_chain_review:issue:c")
async def review_issue_from_risk(request: Request):
    return response_ok(await risk_to_review_issue(tenant_id(request), _json(request), operator_name(request)))


@supply_chain_review_action_bp.post("/disposal-task/confirm")
@openapi.definition(summary="确认执行处置任务")
@requires_permission("supply_chain_review:disposal:u")
async def disposal_task_confirm(request: Request):
    return response_ok(await confirm_disposal_task(tenant_id(request), _json(request), operator_name(request)))


@supply_chain_review_action_bp.post("/disposal-task/retry")
@openapi.definition(summary="重试处置任务")
@requires_permission("supply_chain_review:disposal:u")
async def disposal_task_retry(request: Request):
    return response_ok(await confirm_disposal_task(tenant_id(request), _json(request), operator_name(request)))


@supply_chain_review_action_bp.post("/disposal-task/cancel")
@openapi.definition(summary="取消处置任务")
@requires_permission("supply_chain_review:disposal:u")
async def disposal_task_cancel(request: Request):
    return response_ok(await cancel_disposal_task(tenant_id(request), _json(request), operator_name(request)))


@supply_chain_review_action_bp.post("/notification-event/generate")
@openapi.definition(summary="生成通知事件负载")
@requires_permission("supply_chain_review:notify:c")
async def notification_event_generate(request: Request):
    return response_ok(await create_notification_event(tenant_id(request), _json(request), operator_name(request)))


@supply_chain_review_attachment_bp.post("/attachment-ref/upload")
@openapi.definition(summary="上传安全管理附件")
@requires_permission("supply_chain_review:attachment:c")
async def attachment_ref_upload(request: Request):
    data = _json(request) or _form_data(request)
    file = request.files.get("file") if request.files else None
    if not file:
        raise CustomError(ErrorCode.PARAM_ERROR)
    file_id = await upload_to_s3(file.body, "user", file.name, "supply_chain_review", getattr(file, "type", None))
    payload = {
        **data,
        "file_id": file_id,
        "file_name": file.name,
        "file_type": getattr(file, "type", None),
        "file_size": len(file.body or b""),
    }
    return response_ok(await create_attachment_ref(tenant_id(request), payload, operator_name(request)))


@supply_chain_review_attachment_bp.post("/attachment-ref/download-url")
@openapi.definition(summary="获取安全管理附件下载链接")
@requires_permission("supply_chain_review:attachment:r")
async def attachment_ref_download_url(request: Request):
    record = await get_process_record_or_404(tenant_id(request), "attachment-ref", _json(request).get("id"))
    url = await get_url_from_s3("user", _s3_file_name(record.file_id, "supply_chain_review"), request.host, "supply_chain_review")
    return response_ok({"url": url, "file_id": record.file_id, "file_name": record.file_name})
