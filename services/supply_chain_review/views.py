# coding=utf-8
from __future__ import annotations

import datetime
import math

from sanic import Blueprint, Request
from sanic_ext import openapi
from tortoise.expressions import Q
from tortoise.functions import Count

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import check_permissions, requires_permission
from libs.utils import get_url_from_s3, upload_to_s3
from models.supply_chain_review import (
    SupplyChainCi,
    SupplyChainCiFieldDef,
    SupplyChainCiModelGroup,
    SupplyChainCiType,
    SupplyChainCiUniqueRule,
    SupplyChainPolicyCompareItem,
)
from models.user import Account, Tenant
from services.supply_chain_review.builtin_catalog import (
    apply_builtin_activation,
    builtin_catalog_status,
    complete_builtin_onboarding,
    delete_model_group,
    ensure_builtin_catalog,
    list_model_groups,
    preview_builtin_activation,
    reconcile_builtin_catalog,
    reorder_model_groups,
    save_model_group,
)
from services.supply_chain_review.manager import (
    build_reference_graph,
    build_project_tree,
    cancel_disposal_task,
    ci_directory_options,
    confirm_disposal_task,
    create_attachment_ref,
    create_notification_event,
    delete_ci,
    delete_ci_type,
    delete_actual_policy,
    delete_field,
    delete_ledger_record,
    delete_process_record,
    discover_unknown_terminals,
    export_model_package,
    fields_for_type,
    get_ci_or_404,
    get_ci_type_or_404,
    get_ledger_record_or_404,
    get_process_record_or_404,
    import_model_package,
    list_ledger_records,
    list_process_records,
    normalize_model_package,
    normalize_page,
    operator_name,
    overview_summary,
    overview_todo,
    policy_change_external_callback,
    reference_labels_for_ci,
    reorder_ci_types,
    reorder_fields,
    retry_collection_run,
    risk_to_review_issue,
    run_collection_task,
    run_policy_compare,
    save_ci,
    save_ci_type,
    save_actual_policy,
    save_field,
    save_ledger_record,
    save_process_record,
    save_review_issue as save_review_issue,
    serialize_ci_field,
    sync_actual_policies,
    tenant_id,
    update_compare_item_status,
    transition_review_issue,
    validate_key,
)

supply_chain_review_cmdb_bp = Blueprint(
    "supply_chain_review_cmdb", url_prefix="/supply-chain-review"
)
supply_chain_review_ledger_bp = Blueprint(
    "supply_chain_review_ledger", url_prefix="/supply-chain-review"
)
supply_chain_review_process_bp = Blueprint(
    "supply_chain_review_process", url_prefix="/supply-chain-review"
)
supply_chain_review_action_bp = Blueprint(
    "supply_chain_review_action", url_prefix="/supply-chain-review"
)
supply_chain_review_attachment_bp = Blueprint(
    "supply_chain_review_attachment", url_prefix="/supply-chain-review"
)
network_access_control_bp = Blueprint(
    "network_access_control", url_prefix="/supply-chain-review"
)

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


def _query(request: Request) -> dict:
    return {key: request.args.get(key) for key in request.args}


def _query_bool(data: dict, key: str, default: bool) -> bool:
    value = data.get(key)
    if value in (None, ""):
        return default
    if value in {True, "true", "1", 1}:
        return True
    if value in {False, "false", "0", 0}:
        return False
    raise CustomError(ErrorCode.PARAM_ERROR)


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


def _keyword_condition(keyword: str | None):
    if not keyword:
        return Q()
    return Q(name__icontains=keyword) | Q(key__icontains=keyword)


def _ci_field_filter_request(data: dict) -> dict | None:
    if {"field_key", "field_keyword", "match_mode"} & data.keys():
        raise CustomError(ErrorCode.PARAM_ERROR)
    if "field_filter" not in data:
        return None
    if not isinstance(data["field_filter"], dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    payload = dict(data["field_filter"])

    field_key = payload.get("field_key")
    if not isinstance(field_key, str) or not field_key.strip():
        raise CustomError(ErrorCode.PARAM_ERROR)
    payload["field_key"] = field_key.strip()
    return payload


def _require_ci_field_filter_shape(
    payload: dict,
    allowed: set[str],
    required: set[str],
) -> None:
    if set(payload) - allowed or not required.issubset(payload):
        raise CustomError(ErrorCode.PARAM_ERROR)


def _is_finite_number(value) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _parse_ci_filter_temporal(value, data_type: str):
    if not isinstance(value, str) or not value:
        raise ValueError
    if data_type == "date":
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _strict_equal(left, right) -> bool:
    return type(left) is type(right) and left == right


def _ci_field_enum_values(field) -> list:
    values = []
    for option in getattr(field, "enum_options", None) or []:
        value = option.get("value") if isinstance(option, dict) else option
        if value is not None:
            values.append(value)
    return values


def _normalize_ci_field_filter(field, payload: dict) -> dict:
    data_type = getattr(field, "data_type", None)
    operator = payload.get("operator")
    if data_type == "attachment" or not isinstance(operator, str):
        raise CustomError(ErrorCode.PARAM_ERROR)

    normalized = {
        "field_key": payload["field_key"],
        "data_type": data_type,
    }
    if data_type in {"text", "organization", "user"}:
        _require_ci_field_filter_shape(
            payload,
            {"field_key", "operator", "value"},
            {"field_key", "operator", "value"},
        )
        if operator not in {"contains", "eq"}:
            raise CustomError(ErrorCode.PARAM_ERROR)
        value = payload["value"]
        if not isinstance(value, str) or not value:
            raise CustomError(ErrorCode.PARAM_ERROR)
        return normalized | {"operator": operator, "value": value.casefold()}

    if data_type in {"number", "date", "datetime"}:
        _require_ci_field_filter_shape(
            payload,
            {"field_key", "operator", "lower", "upper"},
            {"field_key", "operator"},
        )
        if operator != "between":
            raise CustomError(ErrorCode.PARAM_ERROR)
        lower = payload.get("lower")
        upper = payload.get("upper")
        if lower is None and upper is None:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if data_type == "number":
            if (lower is not None and not _is_finite_number(lower)) or (
                upper is not None and not _is_finite_number(upper)
            ):
                raise CustomError(ErrorCode.PARAM_ERROR)
        else:
            try:
                lower = (
                    _parse_ci_filter_temporal(lower, data_type)
                    if lower is not None
                    else None
                )
                upper = (
                    _parse_ci_filter_temporal(upper, data_type)
                    if upper is not None
                    else None
                )
            except (TypeError, ValueError):
                raise CustomError(ErrorCode.PARAM_ERROR) from None
        if lower is not None and upper is not None and lower > upper:
            raise CustomError(ErrorCode.PARAM_ERROR)
        return normalized | {
            "operator": "between",
            "lower": lower,
            "upper": upper,
        }

    if data_type in {"enum", "bool", "ref"}:
        _require_ci_field_filter_shape(
            payload,
            {"field_key", "operator", "value"},
            {"field_key", "operator", "value"},
        )
        value = payload["value"]
        if operator != "eq":
            raise CustomError(ErrorCode.PARAM_ERROR)
        if data_type == "enum" and not any(
            _strict_equal(value, option) for option in _ci_field_enum_values(field)
        ):
            raise CustomError(ErrorCode.PARAM_ERROR)
        if data_type == "bool" and type(value) is not bool:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if data_type == "ref" and (not isinstance(value, str) or not value):
            raise CustomError(ErrorCode.PARAM_ERROR)
        return normalized | {"operator": "eq", "value": value}

    if data_type == "tag":
        _require_ci_field_filter_shape(
            payload,
            {"field_key", "operator", "value"},
            {"field_key", "operator", "value"},
        )
        value = payload["value"]
        if (
            operator not in {"contains", "eq"}
            or not isinstance(value, str)
            or not value
        ):
            raise CustomError(ErrorCode.PARAM_ERROR)
        return normalized | {"operator": operator, "value": value.casefold()}

    raise CustomError(ErrorCode.PARAM_ERROR)


async def _resolve_ci_directory_filter(tenant: str, field_filter: dict) -> None:
    data_type = field_filter["data_type"]
    if data_type not in {"organization", "user"}:
        return
    expected = field_filter["value"]
    exact = field_filter["operator"] == "eq"
    if data_type == "organization":
        organizations = await Tenant.filter(id=tenant, deleted=False).all()
        values = [(item.id, [item.id, item.name or item.id]) for item in organizations]
    else:
        accounts = await Account.filter(
            deleted=False,
            disabled=False,
            member__id__in=[tenant],
        ).all()
        values = [
            (
                item.id,
                [item.id, item.username, item.display_name or item.username],
            )
            for item in accounts
        ]
    field_filter["resolved_values"] = {
        item_id
        for item_id, labels in values
        if any(
            (label.casefold() == expected if exact else expected in label.casefold())
            for label in labels
        )
    }


def _matches_ci_field_filter(value, field_filter: dict) -> bool:
    if value is None:
        return False
    data_type = field_filter["data_type"]
    if data_type in {"organization", "user"}:
        return isinstance(value, str) and value in field_filter["resolved_values"]
    if data_type == "text":
        if not isinstance(value, str):
            return False
        actual = value.casefold()
        expected = field_filter["value"]
        return (
            actual == expected
            if field_filter["operator"] == "eq"
            else expected in actual
        )
    if data_type == "number":
        if not _is_finite_number(value):
            return False
        lower = field_filter["lower"]
        upper = field_filter["upper"]
        return (lower is None or value >= lower) and (upper is None or value <= upper)
    if data_type in {"date", "datetime"}:
        try:
            actual = _parse_ci_filter_temporal(value, data_type)
        except (TypeError, ValueError):
            return False
        lower = field_filter["lower"]
        upper = field_filter["upper"]
        return (lower is None or actual >= lower) and (upper is None or actual <= upper)
    if data_type in {"enum", "bool", "ref"}:
        return _strict_equal(value, field_filter["value"])
    if data_type == "tag":
        if not isinstance(value, list):
            return False
        expected = field_filter["value"]
        actual = [item.casefold() for item in value if isinstance(item, str)]
        return (
            expected in actual
            if field_filter["operator"] == "eq"
            else any(expected in item for item in actual)
        )
    return False


async def _require_save_permission(
    request: Request,
    exists: bool,
    create_permission: str,
    update_permission: str,
) -> None:
    await check_permissions(
        request,
        update_permission if exists else create_permission,
    )


async def _require_model_package_permissions(
    request: Request,
    tenant: str,
    package: dict,
) -> tuple[dict, dict[str, set]]:
    normalized = normalize_model_package(package)
    group_keys = {
        validate_key(item.get("key"), "group_key")
        for item in normalized["model_groups"]
    }
    model_keys = {validate_key(item.get("key")) for item in normalized["ci_types"]}
    field_keys = {
        (
            validate_key(item.get("ci_type_key"), "ci_type_key"),
            validate_key(item.get("field_key"), "field_key"),
        )
        for item in normalized["fields"]
    }
    unique_rule_keys = {
        (
            validate_key(item.get("ci_type_key"), "ci_type_key"),
            validate_key(item.get("key"), "unique_rule_key"),
        )
        for item in normalized["unique_rules"]
    }

    existing_group_keys = set()
    if group_keys:
        existing_groups = await SupplyChainCiModelGroup.filter(
            tenant_id=tenant,
            key__in=list(group_keys),
            deleted=False,
        ).all()
        existing_group_keys = {item.key for item in existing_groups}

    existing_model_keys = set()
    if model_keys:
        existing_models = await SupplyChainCiType.filter(
            tenant_id=tenant,
            key__in=list(model_keys),
            deleted=False,
        ).all()
        existing_model_keys = {item.key for item in existing_models}

    existing_field_keys = set()
    if field_keys:
        existing_fields = await SupplyChainCiFieldDef.filter(
            tenant_id=tenant,
            ci_type_key__in=list({item[0] for item in field_keys}),
            field_key__in=list({item[1] for item in field_keys}),
            deleted=False,
        ).all()
        existing_field_keys = {
            (item.ci_type_key, item.field_key) for item in existing_fields
        } & field_keys

    existing_unique_rule_keys = set()
    if unique_rule_keys:
        existing_unique_rules = await SupplyChainCiUniqueRule.filter(
            tenant_id=tenant,
            ci_type_key__in=list({item[0] for item in unique_rule_keys}),
            key__in=list({item[1] for item in unique_rule_keys}),
            deleted=False,
        ).all()
        existing_unique_rule_keys = {
            (item.ci_type_key, item.key) for item in existing_unique_rules
        } & unique_rule_keys

    resources = (
        ("model", group_keys, existing_group_keys),
        ("model", model_keys, existing_model_keys),
        ("model", unique_rule_keys, existing_unique_rule_keys),
        ("field", field_keys, existing_field_keys),
    )
    permission_requirements = {
        resource: {"create": False, "update": False}
        for resource in ("model", "field")
    }
    for resource, requested, existing in resources:
        if requested - existing:
            permission_requirements[resource]["create"] = True
        if requested & existing:
            permission_requirements[resource]["update"] = True
    for resource, requirements in permission_requirements.items():
        if requirements["create"]:
            await check_permissions(request, f"supply_chain_review:{resource}:c")
        if requirements["update"]:
            await check_permissions(request, f"supply_chain_review:{resource}:u")
    return normalized, {
        "model_groups": existing_group_keys,
        "ci_types": existing_model_keys,
        "fields": existing_field_keys,
        "unique_rules": existing_unique_rule_keys,
    }


@supply_chain_review_cmdb_bp.get("/cmdb/model-group/list")
@openapi.definition(summary="资产模型分组目录")
@requires_permission("supply_chain_review:model:r")
async def model_group_list(request: Request):
    data = _query(request)
    return response_ok(
        await list_model_groups(
            tenant_id(request),
            keyword=data.get("keyword"),
            status=data.get("status") or "all",
            include_models=_query_bool(data, "include_models", True),
            include_instance_count=_query_bool(data, "include_counts", False),
        )
    )


@supply_chain_review_cmdb_bp.post("/cmdb/model-group/save")
@openapi.definition(summary="保存模型分组")
async def model_group_save(request: Request):
    data = _json(request)
    await _require_save_permission(
        request,
        bool(data.get("id")),
        "supply_chain_review:model:c",
        "supply_chain_review:model:u",
    )
    group = await save_model_group(
        tenant_id(request),
        data,
        operator_name(request),
    )
    return response_ok(group.json)


@supply_chain_review_cmdb_bp.post("/cmdb/model-group/delete")
@openapi.definition(summary="删除空的模型分组")
@requires_permission("supply_chain_review:model:d")
async def model_group_delete(request: Request):
    return response_ok(
        await delete_model_group(tenant_id(request), _json(request).get("id"))
    )


@supply_chain_review_cmdb_bp.post("/cmdb/model-group/reorder")
@openapi.definition(summary="调整模型分组展示顺序")
@requires_permission("supply_chain_review:model:u")
async def model_group_reorder(request: Request):
    groups = await reorder_model_groups(
        tenant_id(request),
        _json(request).get("group_ids"),
        operator_name(request),
    )
    return response_ok({"items": [group.json for group in groups]})


@supply_chain_review_cmdb_bp.get("/cmdb/builtin-catalog/status")
@openapi.definition(summary="内置资产模型目录状态")
@requires_permission("supply_chain_review:model:r")
async def catalog_status(request: Request):
    return response_ok(
        await builtin_catalog_status(tenant_id(request), operator_name(request))
    )


@supply_chain_review_cmdb_bp.post("/cmdb/builtin-catalog/reconcile")
@openapi.definition(summary="协调内置资产模型目录")
@requires_permission("supply_chain_review:builtin-catalog:admin")
async def catalog_reconcile(request: Request):
    return response_ok(
        await reconcile_builtin_catalog(tenant_id(request), operator_name(request))
    )


@supply_chain_review_cmdb_bp.post("/cmdb/builtin-activation/preview")
@openapi.definition(summary="预览资产模型启停影响")
@requires_permission("supply_chain_review:builtin-activation:u")
async def builtin_activation_preview(request: Request):
    return response_ok(
        await preview_builtin_activation(
            tenant_id(request),
            _json(request),
            operator_name(request),
        )
    )


@supply_chain_review_cmdb_bp.post("/cmdb/builtin-activation/apply")
@openapi.definition(summary="应用资产模型启停")
@requires_permission("supply_chain_review:builtin-activation:u")
async def builtin_activation_apply(request: Request):
    return response_ok(
        await apply_builtin_activation(
            tenant_id(request),
            _json(request),
            operator_name(request),
        )
    )


@supply_chain_review_cmdb_bp.post("/cmdb/builtin-activation/onboarding-complete")
@openapi.definition(summary="完成资产模型首次启用引导")
@requires_permission("supply_chain_review:builtin-activation:u")
async def builtin_activation_onboarding_complete(request: Request):
    return response_ok(
        await complete_builtin_onboarding(
            tenant_id(request),
            operator_name(request),
        )
    )


@supply_chain_review_cmdb_bp.get("/cmdb/directory/options")
@openapi.definition(summary="资产组织与用户目录选项")
@requires_permission("supply_chain_review:model:r")
async def cmdb_directory_options(request: Request):
    return response_ok(await ci_directory_options(tenant_id(request)))


@supply_chain_review_cmdb_bp.post("/ci-type/list")
@openapi.definition(summary="CMDB模型列表")
@requires_permission("supply_chain_review:model:r")
async def ci_type_list(request: Request):
    data = _json(request)
    page, size, offset = normalize_page(data)
    tenant = tenant_id(request)
    await ensure_builtin_catalog(tenant, operator_name(request))
    condition = Q(tenant_id=tenant, deleted=False) & _keyword_condition(
        data.get("keyword")
    )
    if data.get("status"):
        condition &= Q(status=data["status"])
    if data.get("group_id"):
        condition &= Q(group_id=data["group_id"])
    if data.get("group_key"):
        condition &= Q(group_key=data["group_key"])
    queryset = SupplyChainCiType.filter(condition)
    total = await queryset.count()
    items = await queryset.order_by("sort", "key").offset(offset).limit(size).all()
    payload_items = [item.json for item in items]
    if data.get("include_instance_count") and items:
        counts = await (
            SupplyChainCi.filter(
                tenant_id=tenant,
                ci_type_id__in=[item.id for item in items],
                deleted=False,
            )
            .annotate(instance_count=Count("id"))
            .group_by("ci_type_id")
            .values("ci_type_id", "instance_count")
        )
        counts_by_type = {row["ci_type_id"]: row["instance_count"] for row in counts}
        for item in payload_items:
            item["instance_count"] = counts_by_type.get(item["id"], 0)
    return response_ok(
        {
            "total": total,
            "items": payload_items,
            "page": page,
            "size": size,
        }
    )


@supply_chain_review_cmdb_bp.post("/ci-type/detail")
@openapi.definition(summary="CMDB模型详情")
@requires_permission("supply_chain_review:model:r")
async def ci_type_detail(request: Request):
    data = _json(request)
    ci_type = await get_ci_type_or_404(
        tenant_id(request), data.get("id") or data.get("key")
    )
    fields = await fields_for_type(ci_type.tenant_id, ci_type.key)
    payload = ci_type.json
    payload["fields"] = [
        serialize_ci_field(field, ci_type.primary_field) for field in fields
    ]
    return response_ok(payload)


@supply_chain_review_cmdb_bp.post("/ci-type/save")
@openapi.definition(summary="保存CMDB模型")
async def ci_type_save(request: Request):
    data = _json(request)
    tenant = tenant_id(request)
    is_update = bool(data.get("id"))
    await _require_save_permission(
        request,
        is_update,
        "supply_chain_review:model:c",
        "supply_chain_review:model:u",
    )
    item = await save_ci_type(
        tenant,
        data,
        operator_name(request),
        expected_exists=is_update,
    )
    return response_ok(item.json)


@supply_chain_review_cmdb_bp.post("/ci-type/reorder")
@openapi.definition(summary="调整CMDB模型顺序")
@requires_permission("supply_chain_review:model:u")
async def ci_type_reorder(request: Request):
    data = _json(request)
    models = await reorder_ci_types(
        tenant_id(request),
        data.get("ci_type_ids"),
        operator_name(request),
    )
    return response_ok(
        {
            "items": [model.json for model in models],
            "total": len(models),
        }
    )


@supply_chain_review_cmdb_bp.post("/ci-type/delete")
@openapi.definition(summary="删除CMDB模型")
@requires_permission("supply_chain_review:model:d")
async def ci_type_delete(request: Request):
    data = _json(request)
    return response_ok(
        await delete_ci_type(
            tenant_id(request),
            data.get("id") or data.get("key"),
        )
    )


@supply_chain_review_cmdb_bp.post("/ci-type/export")
@openapi.definition(summary="导出CMDB模型包")
@requires_permission("supply_chain_review:model:r")
async def ci_type_export(request: Request):
    data = _json(request)
    return response_ok(
        await export_model_package(
            tenant_id(request),
            ci_type_keys=data.get("ci_type_keys"),
            include_related=bool(data.get("include_related", False)),
        )
    )


@supply_chain_review_cmdb_bp.post("/ci-type/import")
@openapi.definition(summary="导入CMDB模型包")
async def ci_type_import(request: Request):
    data = _json(request)
    package = data.get("package") or data
    if not isinstance(package, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    tenant = tenant_id(request)
    normalized, expected_existing = await _require_model_package_permissions(
        request,
        tenant,
        package,
    )
    return response_ok(
        await import_model_package(
            tenant,
            normalized,
            operator_name(request),
            expected_existing=expected_existing,
        )
    )


@supply_chain_review_cmdb_bp.post("/ci-field/list")
@openapi.definition(summary="CMDB字段列表")
@requires_permission("supply_chain_review:field:r")
async def ci_field_list(request: Request):
    data = _json(request)
    ci_type_ref = data.get("ci_type_id") or data.get("ci_type_key")
    if not ci_type_ref:
        raise CustomError(ErrorCode.PARAM_ERROR)
    tenant = tenant_id(request)
    ci_type = await get_ci_type_or_404(tenant, ci_type_ref)
    fields = await fields_for_type(tenant, ci_type.key)
    return response_ok(
        {
            "items": [
                serialize_ci_field(item, ci_type.primary_field) for item in fields
            ],
            "total": len(fields),
        }
    )


@supply_chain_review_cmdb_bp.post("/ci-field/save")
@openapi.definition(summary="保存CMDB字段")
async def ci_field_save(request: Request):
    data = _json(request)
    tenant = tenant_id(request)
    is_update = bool(data.get("id"))
    await _require_save_permission(
        request,
        is_update,
        "supply_chain_review:field:c",
        "supply_chain_review:field:u",
    )
    item = await save_field(
        tenant,
        data,
        operator_name(request),
        expected_exists=is_update,
    )
    ci_type = await get_ci_type_or_404(
        tenant, getattr(item, "ci_type_id", None) or item.ci_type_key
    )
    return response_ok(serialize_ci_field(item, ci_type.primary_field))


@supply_chain_review_cmdb_bp.post("/ci-field/reorder")
@openapi.definition(summary="调整CMDB字段顺序")
@requires_permission("supply_chain_review:field:u")
async def ci_field_reorder(request: Request):
    data = _json(request)
    tenant = tenant_id(request)
    ci_type_ref = data.get("ci_type_id") or data.get("ci_type_key")
    fields = await reorder_fields(
        tenant,
        ci_type_ref,
        data.get("field_ids"),
        operator_name(request),
    )
    ci_type = await get_ci_type_or_404(tenant, ci_type_ref)
    return response_ok(
        {
            "items": [
                serialize_ci_field(field, ci_type.primary_field) for field in fields
            ],
            "total": len(fields),
        }
    )


@supply_chain_review_cmdb_bp.post("/ci-field/delete")
@openapi.definition(summary="删除CMDB字段")
@requires_permission("supply_chain_review:field:d")
async def ci_field_delete(request: Request):
    return response_ok(
        await delete_field(
            tenant_id(request),
            _json(request).get("id"),
        )
    )


@supply_chain_review_cmdb_bp.post("/ci/list")
@openapi.definition(summary="CI实例列表")
@requires_permission("supply_chain_review:ci:r")
async def ci_list(request: Request):
    data = _json(request)
    page, size, offset = normalize_page(data)
    filter_request = _ci_field_filter_request(data)
    tenant = tenant_id(request)
    ci_type = None
    condition = Q(tenant_id=tenant, deleted=False)
    if data.get("ci_type_id") or data.get("ci_type_key"):
        ci_type = await get_ci_type_or_404(
            tenant, data.get("ci_type_id") or data.get("ci_type_key")
        )
        condition &= Q(ci_type_id=ci_type.id)
    for field in ["project_area_id", "accessory_project_id", "status", "risk_status"]:
        if data.get(field):
            condition &= Q(**{field: data[field]})
    if data.get("keyword"):
        condition &= Q(name__icontains=data["keyword"])
    queryset = SupplyChainCi.filter(condition)

    if filter_request is not None:
        if ci_type is None:
            raise CustomError(ErrorCode.PARAM_ERROR)
        payload = filter_request
        fields = await fields_for_type(tenant, str(ci_type.id))
        field = next(
            (item for item in fields if item.field_key == payload["field_key"]),
            None,
        )
        if field is None:
            raise CustomError(ErrorCode.PARAM_ERROR)
        field_filter = _normalize_ci_field_filter(field, payload)
        await _resolve_ci_directory_filter(tenant, field_filter)
        matched_items = []
        for item in await queryset.order_by("-utime").all():
            value = (item.attributes or {}).get(field_filter["field_key"])
            if _matches_ci_field_filter(value, field_filter):
                matched_items.append(item)
        total = len(matched_items)
        items = matched_items[offset : offset + size]
        return response_ok(
            {
                "total": total,
                "items": [item.json for item in items],
                "page": page,
                "size": size,
            }
        )

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


@supply_chain_review_cmdb_bp.post("/ci/detail")
@openapi.definition(summary="CI实例详情")
@requires_permission("supply_chain_review:ci:r")
async def ci_detail(request: Request):
    ci = await get_ci_or_404(
        tenant_id(request), _json(request).get("id") or _json(request).get("ci_id")
    )
    fields = await fields_for_type(ci.tenant_id, ci.ci_type_key)
    payload = ci.json
    payload["fields"] = [item.json for item in fields]
    payload["reference_labels"] = await reference_labels_for_ci(
        ci.tenant_id, ci, fields
    )
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
    data = _json(request)
    return response_ok(
        await delete_ci(
            tenant_id(request),
            data.get("id") or data.get("ci_id"),
        )
    )


@supply_chain_review_cmdb_bp.post("/ci/import")
@openapi.definition(summary="批量导入CI实例")
@requires_permission("supply_chain_review:ci:c")
async def ci_import(request: Request):
    data = _json(request)
    records = data.get("items") or data.get("records") or []
    if not isinstance(records, list):
        raise CustomError(ErrorCode.PARAM_ERROR)
    target_ci_type = data.get("ci_type_id") or data.get("ci_type_key")
    imported = []
    for record in records:
        if not isinstance(record, dict):
            raise CustomError(ErrorCode.PARAM_ERROR)
        record = {
            **record,
            "ci_type_id": target_ci_type
            or record.get("ci_type_id")
            or record.get("ci_type_key"),
        }
        if target_ci_type:
            record.pop("id", None)
            record.pop("ci_type_key", None)
        imported.append(
            (await save_ci(tenant_id(request), record, operator_name(request))).json
        )
    return response_ok({"total": len(imported), "items": imported})


@supply_chain_review_cmdb_bp.post("/ci/import-template")
@openapi.definition(summary="获取CI导入模板")
@requires_permission("supply_chain_review:ci:r")
async def ci_import_template(request: Request):
    data = _json(request)
    ci_type = await get_ci_type_or_404(
        tenant_id(request), data.get("ci_type_id") or data.get("ci_type_key")
    )
    fields = await fields_for_type(ci_type.tenant_id, ci_type.key)
    columns = [
        {"field_key": "name", "label": "名称", "required": True, "data_type": "text"},
        {
            "field_key": "project_area_id",
            "label": "厂区CI ID",
            "required": False,
            "data_type": "text",
        },
        {
            "field_key": "accessory_project_id",
            "label": "配件项目CI ID",
            "required": False,
            "data_type": "text",
        },
    ]
    columns.extend([field.json for field in fields])
    return response_ok({"ci_type": ci_type.json, "columns": columns})


@supply_chain_review_cmdb_bp.post("/ci/reference-graph")
@openapi.definition(summary="CI引用拓扑")
@requires_permission("supply_chain_review:ci:r")
async def ci_reference_graph(request: Request):
    data = _json(request)
    return response_ok(
        await build_reference_graph(
            tenant_id(request), data.get("ci_id"), data.get("depth", 1)
        )
    )


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
    supply_chain_review_ledger_bp.add_route(
        requires_permission(f"supply_chain_review:{resource}:r")(
            openapi.definition(summary=f"{title}列表")(list_view)
        ),
        f"/{path}/list",
        methods=["POST"],
    )
    supply_chain_review_ledger_bp.add_route(
        requires_permission(f"supply_chain_review:{resource}:r")(
            openapi.definition(summary=f"{title}详情")(detail_view)
        ),
        f"/{path}/detail",
        methods=["POST"],
    )
    supply_chain_review_ledger_bp.add_route(
        requires_permission(
            f"supply_chain_review:{resource}:c", f"supply_chain_review:{resource}:u"
        )(openapi.definition(summary=f"保存{title}")(save_view)),
        f"/{path}/save",
        methods=["POST"],
    )
    supply_chain_review_ledger_bp.add_route(
        requires_permission(f"supply_chain_review:{resource}:d")(
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
    blueprint: Blueprint = supply_chain_review_process_bp,
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
            f"supply_chain_review:{resource}:{'u' if data.get('id') else 'c'}",
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
        requires_permission(f"supply_chain_review:{resource}:r")(
            openapi.definition(summary=f"{title}列表")(list_view)
        ),
        f"/{path}/list",
        methods=["POST"],
    )
    blueprint.add_route(
        requires_permission(f"supply_chain_review:{resource}:r")(
            openapi.definition(summary=f"{title}详情")(detail_view)
        ),
        f"/{path}/detail",
        methods=["POST"],
    )
    if not readonly:
        blueprint.add_route(
            requires_permission(
                f"supply_chain_review:{resource}:c", f"supply_chain_review:{resource}:u"
            )(openapi.definition(summary=f"保存{title}")(save_view)),
            f"/{path}/save",
            methods=["POST"],
        )
        blueprint.add_route(
            requires_permission(f"supply_chain_review:{resource}:d")(
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


@supply_chain_review_action_bp.post("/collection-task/run")
@openapi.definition(summary="运行采集任务")
@requires_permission("supply_chain_review:collection:u")
async def collection_task_run(request: Request):
    return response_ok(
        await run_collection_task(
            tenant_id(request),
            _json(request).get("id") or _json(request).get("task_id"),
            operator_name(request),
        )
    )


@supply_chain_review_action_bp.post("/collection-run/retry")
@openapi.definition(summary="重试采集运行")
@requires_permission("supply_chain_review:collection:u")
async def collection_run_retry(request: Request):
    return response_ok(
        await retry_collection_run(
            tenant_id(request), _json(request).get("id"), operator_name(request)
        )
    )


@network_access_control_bp.post("/actual-policy/sync")
@openapi.definition(summary="批量同步访问控制策略并记录历史")
@requires_permission("supply_chain_review:policy:c", "supply_chain_review:policy:u")
async def actual_policy_sync(request: Request):
    await check_permissions(request, "supply_chain_review:policy:c")
    await check_permissions(request, "supply_chain_review:policy:u")
    if _json(request).get("replace") is True:
        await check_permissions(request, "supply_chain_review:policy:d")
    return response_ok(
        await sync_actual_policies(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@supply_chain_review_action_bp.post("/policy-compare/run")
@openapi.definition(summary="运行策略比对")
@requires_permission("supply_chain_review:policy:u")
async def policy_compare_run(request: Request):
    return response_ok(
        await run_policy_compare(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@supply_chain_review_action_bp.post("/policy-compare/run-list")
@openapi.definition(summary="策略比对运行列表")
@requires_permission("supply_chain_review:policy:r")
async def policy_compare_run_list(request: Request):
    return response_ok(
        await list_process_records(
            tenant_id(request), "policy-compare-run", _json(request)
        )
    )


@supply_chain_review_action_bp.post("/policy-compare/item-list")
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
    return response_ok(
        {
            "total": total,
            "items": [item.json for item in items],
            "page": page,
            "size": size,
        }
    )


@supply_chain_review_action_bp.post("/policy-compare/item-confirm")
@openapi.definition(summary="确认策略比对明细")
@requires_permission("supply_chain_review:policy:u")
async def policy_compare_item_confirm(request: Request):
    return response_ok(
        await update_compare_item_status(
            tenant_id(request),
            _json(request).get("id"),
            "confirmed",
            operator_name(request),
        )
    )


@supply_chain_review_action_bp.post("/policy-compare/item-ignore")
@openapi.definition(summary="忽略策略比对明细")
@requires_permission("supply_chain_review:policy:u")
async def policy_compare_item_ignore(request: Request):
    return response_ok(
        await update_compare_item_status(
            tenant_id(request),
            _json(request).get("id"),
            "ignored",
            operator_name(request),
        )
    )


@supply_chain_review_action_bp.post("/policy-change/external-callback")
@openapi.definition(summary="策略变更外部审批结果回写")
@requires_permission("supply_chain_review:policy:u")
async def policy_change_callback(request: Request):
    return response_ok(
        await policy_change_external_callback(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@supply_chain_review_action_bp.post("/unknown-terminal/discover")
@openapi.definition(summary="网络侧未知终端发现")
@requires_permission("supply_chain_review:terminal:c")
async def unknown_terminal_discover(request: Request):
    return response_ok(
        await discover_unknown_terminals(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@supply_chain_review_action_bp.post("/review-issue/transition")
@openapi.definition(summary="审查问题状态流转")
@requires_permission("supply_chain_review:issue:u")
async def review_issue_transition(request: Request):
    return response_ok(
        await transition_review_issue(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@supply_chain_review_action_bp.post("/review-issue/from-risk")
@openapi.definition(summary="风险转审查问题")
@requires_permission("supply_chain_review:issue:c")
async def review_issue_from_risk(request: Request):
    return response_ok(
        await risk_to_review_issue(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@supply_chain_review_action_bp.post("/disposal-task/confirm")
@openapi.definition(summary="确认执行处置任务")
@requires_permission("supply_chain_review:disposal:u")
async def disposal_task_confirm(request: Request):
    return response_ok(
        await confirm_disposal_task(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@supply_chain_review_action_bp.post("/disposal-task/retry")
@openapi.definition(summary="重试处置任务")
@requires_permission("supply_chain_review:disposal:u")
async def disposal_task_retry(request: Request):
    return response_ok(
        await confirm_disposal_task(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@supply_chain_review_action_bp.post("/disposal-task/cancel")
@openapi.definition(summary="取消处置任务")
@requires_permission("supply_chain_review:disposal:u")
async def disposal_task_cancel(request: Request):
    return response_ok(
        await cancel_disposal_task(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@supply_chain_review_action_bp.post("/notification-event/generate")
@openapi.definition(summary="生成通知事件负载")
@requires_permission("supply_chain_review:notify:c")
async def notification_event_generate(request: Request):
    return response_ok(
        await create_notification_event(
            tenant_id(request), _json(request), operator_name(request)
        )
    )


@supply_chain_review_attachment_bp.post("/attachment-ref/upload")
@openapi.definition(summary="上传安全管理附件")
@requires_permission("supply_chain_review:attachment:c")
async def attachment_ref_upload(request: Request):
    data = _json(request) or _form_data(request)
    file = request.files.get("file") if request.files else None
    if not file:
        raise CustomError(ErrorCode.PARAM_ERROR)
    file_id = await upload_to_s3(
        file.body, "user", file.name, "supply_chain_review", getattr(file, "type", None)
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


@supply_chain_review_attachment_bp.post("/attachment-ref/download-url")
@openapi.definition(summary="获取安全管理附件下载链接")
@requires_permission("supply_chain_review:attachment:r")
async def attachment_ref_download_url(request: Request):
    record = await get_process_record_or_404(
        tenant_id(request), "attachment-ref", _json(request).get("id")
    )
    url = await get_url_from_s3(
        "user",
        _s3_file_name(record.file_id, "supply_chain_review"),
        request.host,
        "supply_chain_review",
    )
    return response_ok(
        {"url": url, "file_id": record.file_id, "file_name": record.file_name}
    )
