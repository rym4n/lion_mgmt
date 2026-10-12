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
from models.asset_management import (
    AssetFieldDefinition,
    AssetInstance,
    AssetModel,
    AssetModelGroup,
    AssetUniqueRule,
)
from models.user import Account, Tenant
from services.asset_management.builtin_catalog import (
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
from services.asset_management.manager import (
    asset_directory_options,
    build_reference_graph,
    delete_asset_instance,
    delete_asset_instances,
    delete_asset_model,
    delete_field,
    export_asset_model_package,
    fields_for_model,
    get_asset_instance_or_404,
    get_asset_model_or_404,
    import_asset_model_package,
    normalize_asset_model_package,
    normalize_page,
    operator_name,
    reference_labels_for_asset,
    reference_labels_for_assets,
    reorder_asset_models,
    reorder_fields,
    save_asset_instance,
    save_asset_model,
    save_field,
    serialize_asset_field,
    tenant_id,
    validate_key,
)

asset_management_bp = Blueprint("asset_management", url_prefix="/asset-management")
__all__ = ["asset_management_bp"]


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


def _asset_field_filter_request(data: dict) -> dict | None:
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


def _require_asset_field_filter_shape(
    payload: dict,
    allowed: set[str],
    required: set[str],
) -> None:
    if set(payload) - allowed or not required.issubset(payload):
        raise CustomError(ErrorCode.PARAM_ERROR)


def _is_finite_number(value) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _parse_asset_filter_temporal(value, data_type: str):
    if not isinstance(value, str) or not value:
        raise ValueError
    if data_type == "date":
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _strict_equal(left, right) -> bool:
    return type(left) is type(right) and left == right


def _asset_field_enum_values(field) -> list:
    values = []
    for option in getattr(field, "enum_options", None) or []:
        value = option.get("value") if isinstance(option, dict) else option
        if value is not None:
            values.append(value)
    return values


def _normalize_asset_field_filter(field, payload: dict) -> dict:
    data_type = getattr(field, "data_type", None)
    operator = payload.get("operator")
    if data_type == "attachment" or not isinstance(operator, str):
        raise CustomError(ErrorCode.PARAM_ERROR)

    normalized = {
        "field_key": payload["field_key"],
        "data_type": data_type,
    }
    if data_type in {"text", "organization", "user"}:
        _require_asset_field_filter_shape(
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
        _require_asset_field_filter_shape(
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
                    _parse_asset_filter_temporal(lower, data_type)
                    if lower is not None
                    else None
                )
                upper = (
                    _parse_asset_filter_temporal(upper, data_type)
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
        _require_asset_field_filter_shape(
            payload,
            {"field_key", "operator", "value"},
            {"field_key", "operator", "value"},
        )
        value = payload["value"]
        if operator != "eq":
            raise CustomError(ErrorCode.PARAM_ERROR)
        if data_type == "enum" and not any(
            _strict_equal(value, option) for option in _asset_field_enum_values(field)
        ):
            raise CustomError(ErrorCode.PARAM_ERROR)
        if data_type == "bool" and type(value) is not bool:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if data_type == "ref" and (not isinstance(value, str) or not value):
            raise CustomError(ErrorCode.PARAM_ERROR)
        return normalized | {"operator": "eq", "value": value}

    if data_type == "tag":
        _require_asset_field_filter_shape(
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


async def _resolve_asset_directory_filter(tenant: str, field_filter: dict) -> None:
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


def _matches_asset_field_filter(value, field_filter: dict) -> bool:
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
            actual = _parse_asset_filter_temporal(value, data_type)
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
    normalized = normalize_asset_model_package(package)
    group_keys = {
        validate_key(item.get("key"), "group_key")
        for item in normalized["asset_model_groups"]
    }
    model_keys = {validate_key(item.get("key")) for item in normalized["asset_models"]}
    field_keys = {
        (
            validate_key(item.get("asset_model_key"), "asset_model_key"),
            validate_key(item.get("field_key"), "field_key"),
        )
        for item in normalized["asset_fields"]
    }
    unique_rule_keys = {
        (
            validate_key(item.get("asset_model_key"), "asset_model_key"),
            validate_key(item.get("key"), "unique_rule_key"),
        )
        for item in normalized["asset_unique_rules"]
    }

    existing_group_keys = set()
    if group_keys:
        existing_groups = await AssetModelGroup.filter(
            tenant_id=tenant,
            key__in=list(group_keys),
            deleted=False,
        ).all()
        existing_group_keys = {item.key for item in existing_groups}

    existing_model_keys = set()
    if model_keys:
        existing_models = await AssetModel.filter(
            tenant_id=tenant,
            key__in=list(model_keys),
            deleted=False,
        ).all()
        existing_model_keys = {item.key for item in existing_models}

    existing_field_keys = set()
    if field_keys:
        existing_fields = await AssetFieldDefinition.filter(
            tenant_id=tenant,
            asset_model_key__in=list({item[0] for item in field_keys}),
            field_key__in=list({item[1] for item in field_keys}),
            deleted=False,
        ).all()
        existing_field_keys = {
            (item.asset_model_key, item.field_key) for item in existing_fields
        } & field_keys

    existing_unique_rule_keys = set()
    if unique_rule_keys:
        existing_unique_rules = await AssetUniqueRule.filter(
            tenant_id=tenant,
            asset_model_key__in=list({item[0] for item in unique_rule_keys}),
            key__in=list({item[1] for item in unique_rule_keys}),
            deleted=False,
        ).all()
        existing_unique_rule_keys = {
            (item.asset_model_key, item.key) for item in existing_unique_rules
        } & unique_rule_keys

    resources = (
        ("model", group_keys, existing_group_keys),
        ("model", model_keys, existing_model_keys),
        ("model", unique_rule_keys, existing_unique_rule_keys),
        ("field", field_keys, existing_field_keys),
    )
    permission_requirements = {
        resource: {"create": False, "update": False} for resource in ("model", "field")
    }
    for resource, requested, existing in resources:
        if requested - existing:
            permission_requirements[resource]["create"] = True
        if requested & existing:
            permission_requirements[resource]["update"] = True
    for resource, requirements in permission_requirements.items():
        if requirements["create"]:
            await check_permissions(request, "asset_management:c")
        if requirements["update"]:
            await check_permissions(request, "asset_management:u")
    return normalized, {
        "asset_model_groups": existing_group_keys,
        "asset_models": existing_model_keys,
        "asset_fields": existing_field_keys,
        "asset_unique_rules": existing_unique_rule_keys,
    }


@asset_management_bp.get("/model-group/list")
@openapi.definition(summary="资产模型分组目录")
@requires_permission("asset_management:r")
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


@asset_management_bp.post("/model-group/save")
@openapi.definition(summary="保存模型分组")
async def model_group_save(request: Request):
    data = _json(request)
    await _require_save_permission(
        request,
        bool(data.get("id")),
        "asset_management:c",
        "asset_management:u",
    )
    group = await save_model_group(
        tenant_id(request),
        data,
        operator_name(request),
    )
    return response_ok(group.json)


@asset_management_bp.post("/model-group/delete")
@openapi.definition(summary="删除空的模型分组")
@requires_permission("asset_management:d")
async def model_group_delete(request: Request):
    return response_ok(
        await delete_model_group(tenant_id(request), _json(request).get("id"))
    )


@asset_management_bp.post("/model-group/reorder")
@openapi.definition(summary="调整模型分组展示顺序")
@requires_permission("asset_management:u")
async def model_group_reorder(request: Request):
    groups = await reorder_model_groups(
        tenant_id(request),
        _json(request).get("group_ids"),
        operator_name(request),
    )
    return response_ok({"items": [group.json for group in groups]})


@asset_management_bp.get("/builtin-catalog/status")
@openapi.definition(summary="内置资产模型目录状态")
@requires_permission("asset_management:r")
async def catalog_status(request: Request):
    return response_ok(
        await builtin_catalog_status(tenant_id(request), operator_name(request))
    )


@asset_management_bp.post("/builtin-catalog/reconcile")
@openapi.definition(summary="协调内置资产模型目录")
@requires_permission("asset_management:u")
async def catalog_reconcile(request: Request):
    return response_ok(
        await reconcile_builtin_catalog(tenant_id(request), operator_name(request))
    )


@asset_management_bp.post("/builtin-activation/preview")
@openapi.definition(summary="预览资产模型启停影响")
@requires_permission("asset_management:u")
async def builtin_activation_preview(request: Request):
    return response_ok(
        await preview_builtin_activation(
            tenant_id(request),
            _json(request),
            operator_name(request),
        )
    )


@asset_management_bp.post("/builtin-activation/apply")
@openapi.definition(summary="应用资产模型启停")
@requires_permission("asset_management:u")
async def builtin_activation_apply(request: Request):
    return response_ok(
        await apply_builtin_activation(
            tenant_id(request),
            _json(request),
            operator_name(request),
        )
    )


@asset_management_bp.post("/builtin-activation/onboarding-complete")
@openapi.definition(summary="完成资产模型首次启用引导")
@requires_permission("asset_management:u")
async def builtin_activation_onboarding_complete(request: Request):
    return response_ok(
        await complete_builtin_onboarding(
            tenant_id(request),
            operator_name(request),
        )
    )


@asset_management_bp.get("/directory/options")
@openapi.definition(summary="资产组织与用户目录选项")
@requires_permission("asset_management:r")
async def asset_directory_options_view(request: Request):
    return response_ok(await asset_directory_options(tenant_id(request)))


@asset_management_bp.post("/asset-model/list")
@openapi.definition(summary="资产模型列表")
@requires_permission("asset_management:r")
async def asset_model_list(request: Request):
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
    queryset = AssetModel.filter(condition)
    total = await queryset.count()
    items = await queryset.order_by("sort", "key").offset(offset).limit(size).all()
    payload_items = [item.json for item in items]
    if data.get("include_instance_count") and items:
        counts = await (
            AssetInstance.filter(
                tenant_id=tenant,
                asset_model_id__in=[item.id for item in items],
                deleted=False,
            )
            .annotate(instance_count=Count("id"))
            .group_by("asset_model_id")
            .values("asset_model_id", "instance_count")
        )
        counts_by_type = {
            row["asset_model_id"]: row["instance_count"] for row in counts
        }
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


@asset_management_bp.post("/asset-model/detail")
@openapi.definition(summary="资产模型详情")
@requires_permission("asset_management:r")
async def asset_model_detail(request: Request):
    data = _json(request)
    asset_model = await get_asset_model_or_404(
        tenant_id(request), data.get("id") or data.get("key")
    )
    fields = await fields_for_model(asset_model.tenant_id, asset_model.key)
    payload = asset_model.json
    payload["fields"] = [
        serialize_asset_field(field, asset_model.primary_field) for field in fields
    ]
    return response_ok(payload)


@asset_management_bp.post("/asset-model/save")
@openapi.definition(summary="保存资产模型")
async def asset_model_save(request: Request):
    data = _json(request)
    tenant = tenant_id(request)
    is_update = bool(data.get("id"))
    await _require_save_permission(
        request,
        is_update,
        "asset_management:c",
        "asset_management:u",
    )
    item = await save_asset_model(
        tenant,
        data,
        operator_name(request),
        expected_exists=is_update,
    )
    return response_ok(item.json)


@asset_management_bp.post("/asset-model/reorder")
@openapi.definition(summary="调整资产模型顺序")
@requires_permission("asset_management:u")
async def asset_model_reorder(request: Request):
    data = _json(request)
    models = await reorder_asset_models(
        tenant_id(request),
        data.get("asset_model_ids"),
        operator_name(request),
    )
    return response_ok(
        {
            "items": [model.json for model in models],
            "total": len(models),
        }
    )


@asset_management_bp.post("/asset-model/delete")
@openapi.definition(summary="删除资产模型")
@requires_permission("asset_management:d")
async def asset_model_delete(request: Request):
    data = _json(request)
    return response_ok(
        await delete_asset_model(
            tenant_id(request),
            data.get("id") or data.get("key"),
        )
    )


@asset_management_bp.post("/asset-model/export")
@openapi.definition(summary="导出资产模型包")
@requires_permission("asset_management:r")
async def asset_model_export(request: Request):
    data = _json(request)
    return response_ok(
        await export_asset_model_package(
            tenant_id(request),
            asset_model_keys=data.get("asset_model_keys"),
            include_related=bool(data.get("include_related", False)),
        )
    )


@asset_management_bp.post("/asset-model/import")
@openapi.definition(summary="导入资产模型包")
async def asset_model_import(request: Request):
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
        await import_asset_model_package(
            tenant,
            normalized,
            operator_name(request),
            expected_existing=expected_existing,
        )
    )


@asset_management_bp.post("/asset-field/list")
@openapi.definition(summary="资产字段列表")
@requires_permission("asset_management:r")
async def asset_field_list(request: Request):
    data = _json(request)
    asset_model_ref = data.get("asset_model_id") or data.get("asset_model_key")
    if not asset_model_ref:
        raise CustomError(ErrorCode.PARAM_ERROR)
    tenant = tenant_id(request)
    asset_model = await get_asset_model_or_404(tenant, asset_model_ref)
    fields = await fields_for_model(tenant, asset_model.key)
    return response_ok(
        {
            "items": [
                serialize_asset_field(item, asset_model.primary_field)
                for item in fields
            ],
            "total": len(fields),
        }
    )


@asset_management_bp.post("/asset-field/save")
@openapi.definition(summary="保存资产字段")
async def asset_field_save(request: Request):
    data = _json(request)
    tenant = tenant_id(request)
    is_update = bool(data.get("id"))
    await _require_save_permission(
        request,
        is_update,
        "asset_management:c",
        "asset_management:u",
    )
    item = await save_field(
        tenant,
        data,
        operator_name(request),
        expected_exists=is_update,
    )
    asset_model = await get_asset_model_or_404(
        tenant, getattr(item, "asset_model_id", None) or item.asset_model_key
    )
    return response_ok(serialize_asset_field(item, asset_model.primary_field))


@asset_management_bp.post("/asset-field/reorder")
@openapi.definition(summary="调整资产字段顺序")
@requires_permission("asset_management:u")
async def asset_field_reorder(request: Request):
    data = _json(request)
    tenant = tenant_id(request)
    asset_model_ref = data.get("asset_model_id") or data.get("asset_model_key")
    fields = await reorder_fields(
        tenant,
        asset_model_ref,
        data.get("field_ids"),
        operator_name(request),
    )
    asset_model = await get_asset_model_or_404(tenant, asset_model_ref)
    return response_ok(
        {
            "items": [
                serialize_asset_field(field, asset_model.primary_field)
                for field in fields
            ],
            "total": len(fields),
        }
    )


@asset_management_bp.post("/asset-field/delete")
@openapi.definition(summary="删除资产字段")
@requires_permission("asset_management:d")
async def asset_field_delete(request: Request):
    return response_ok(
        await delete_field(
            tenant_id(request),
            _json(request).get("id"),
        )
    )


@asset_management_bp.post("/asset/list")
@openapi.definition(summary="资产实例列表")
@requires_permission("asset_management:r")
async def asset_list(request: Request):
    data = _json(request)
    page, size, offset = normalize_page(data)
    filter_request = _asset_field_filter_request(data)
    tenant = tenant_id(request)
    asset_model = None
    fields = None
    condition = Q(tenant_id=tenant, deleted=False)
    if data.get("asset_model_id") or data.get("asset_model_key"):
        asset_model = await get_asset_model_or_404(
            tenant, data.get("asset_model_id") or data.get("asset_model_key")
        )
        condition &= Q(asset_model_id=asset_model.id)
    for field in ["project_area_id", "accessory_project_id", "status", "risk_status"]:
        if data.get(field):
            condition &= Q(**{field: data[field]})
    if data.get("keyword"):
        condition &= Q(name__icontains=data["keyword"])
    queryset = AssetInstance.filter(condition)

    if filter_request is not None:
        if asset_model is None:
            raise CustomError(ErrorCode.PARAM_ERROR)
        payload = filter_request
        fields = await fields_for_model(tenant, str(asset_model.id))
        field = next(
            (item for item in fields if item.field_key == payload["field_key"]),
            None,
        )
        if field is None:
            raise CustomError(ErrorCode.PARAM_ERROR)
        field_filter = _normalize_asset_field_filter(field, payload)
        await _resolve_asset_directory_filter(tenant, field_filter)
        matched_items = []
        for item in await queryset.order_by("-utime").all():
            value = (item.attributes or {}).get(field_filter["field_key"])
            if _matches_asset_field_filter(value, field_filter):
                matched_items.append(item)
        total = len(matched_items)
        items = matched_items[offset : offset + size]
    else:
        total = await queryset.count()
        items = await queryset.order_by("-utime").offset(offset).limit(size).all()

    if fields is None and asset_model is not None:
        fields = await fields_for_model(tenant, str(asset_model.id))
    labels_by_asset = await reference_labels_for_assets(tenant, items, fields)
    payload_items = []
    for item in items:
        payload = dict(item.json)
        payload["reference_labels"] = labels_by_asset.get(str(item.id), {})
        payload_items.append(payload)
    return response_ok(
        {
            "total": total,
            "items": payload_items,
            "page": page,
            "size": size,
        }
    )


@asset_management_bp.post("/asset/detail")
@openapi.definition(summary="资产实例详情")
@requires_permission("asset_management:r")
async def asset_detail(request: Request):
    asset = await get_asset_instance_or_404(
        tenant_id(request), _json(request).get("id") or _json(request).get("asset_id")
    )
    fields = await fields_for_model(asset.tenant_id, asset.asset_model_key)
    payload = asset.json
    payload["fields"] = [item.json for item in fields]
    payload["reference_labels"] = await reference_labels_for_asset(
        asset.tenant_id, asset, fields
    )
    return response_ok(payload)


@asset_management_bp.post("/asset/save")
@openapi.definition(summary="保存资产实例")
@requires_permission("asset_management:c", "asset_management:u")
async def asset_save(request: Request):
    item = await save_asset_instance(
        tenant_id(request), _json(request), operator_name(request)
    )
    return response_ok(item.json)


@asset_management_bp.post("/asset/delete")
@openapi.definition(summary="删除资产实例")
@requires_permission("asset_management:d")
async def asset_delete(request: Request):
    data = _json(request)
    return response_ok(
        await delete_asset_instance(
            tenant_id(request),
            data.get("id") or data.get("asset_id"),
        )
    )


@asset_management_bp.post("/asset/delete-batch")
@openapi.definition(summary="批量删除资产实例")
@requires_permission("asset_management:d")
async def asset_delete_batch(request: Request):
    return response_ok(
        await delete_asset_instances(
            tenant_id(request),
            _json(request).get("ids"),
        )
    )


@asset_management_bp.post("/asset/import")
@openapi.definition(summary="批量导入资产实例")
@requires_permission("asset_management:c")
async def asset_import(request: Request):
    data = _json(request)
    records = data.get("items") or data.get("records") or []
    if not isinstance(records, list):
        raise CustomError(ErrorCode.PARAM_ERROR)
    target_asset_model = data.get("asset_model_id") or data.get("asset_model_key")
    imported = []
    for record in records:
        if not isinstance(record, dict):
            raise CustomError(ErrorCode.PARAM_ERROR)
        record = {
            **record,
            "asset_model_id": target_asset_model
            or record.get("asset_model_id")
            or record.get("asset_model_key"),
        }
        if target_asset_model:
            record.pop("id", None)
            record.pop("asset_model_key", None)
        imported.append(
            (
                await save_asset_instance(
                    tenant_id(request), record, operator_name(request)
                )
            ).json
        )
    return response_ok({"total": len(imported), "items": imported})


@asset_management_bp.post("/asset/import-template")
@openapi.definition(summary="获取资产导入模板")
@requires_permission("asset_management:r")
async def asset_import_template(request: Request):
    data = _json(request)
    asset_model = await get_asset_model_or_404(
        tenant_id(request), data.get("asset_model_id") or data.get("asset_model_key")
    )
    fields = await fields_for_model(asset_model.tenant_id, asset_model.key)
    columns = [
        {"field_key": "name", "label": "名称", "required": True, "data_type": "text"},
        {
            "field_key": "project_area_id",
            "label": "厂区资产 ID",
            "required": False,
            "data_type": "text",
        },
        {
            "field_key": "accessory_project_id",
            "label": "配件项目资产 ID",
            "required": False,
            "data_type": "text",
        },
    ]
    columns.extend([field.json for field in fields])
    return response_ok({"asset_model": asset_model.json, "columns": columns})


@asset_management_bp.post("/asset/reference-graph")
@openapi.definition(summary="资产关系拓扑")
@requires_permission("asset_management:r")
async def asset_reference_graph(request: Request):
    data = _json(request)
    return response_ok(
        await build_reference_graph(
            tenant_id(request), data.get("asset_id"), data.get("depth", 1)
        )
    )
