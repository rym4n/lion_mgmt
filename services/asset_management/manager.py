# coding=utf-8
from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from typing import Iterable

from pypinyin import lazy_pinyin
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.asset_management import (
    FIELD_DATA_TYPES,
    AssetFieldDefinition,
    AssetInstance,
    AssetModel,
    AssetModelGroup,
    AssetReference,
    AssetUniqueRule,
)
from models.user import Account, Tenant

KEY_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
ASSET_MODEL_STATUSES = {"active", "disabled"}
ASSET_STATUSES = {"active", "disabled", "archived"}
RISK_STATUSES = {"normal", "low", "medium", "high"}


def require_dict(data) -> dict:
    if not isinstance(data, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return data


def normalize_page(data: dict) -> tuple[int, int, int]:
    page = max(1, int(data.get("page", 1) or 1))
    size = max(1, min(100, int(data.get("size", data.get("page_size", 20)) or 20)))
    return page, size, (page - 1) * size


def value_matches_keyword(value, keyword: str) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, dict):
        return any(value_matches_keyword(item, keyword) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(value_matches_keyword(item, keyword) for item in value)
    return keyword in str(value).casefold()


def json_matches_keyword(data: dict, keyword: str) -> bool:
    folded = keyword.strip().casefold()
    if not folded:
        return True
    return value_matches_keyword(data, folded)


def validate_key(value: str | None, field: str = "key") -> str:
    value = (value or "").strip()
    if not KEY_RE.match(value):
        raise CustomError(ErrorCode.PARAM_ERROR)
    return value


def operator_name(request) -> str | None:
    account = getattr(request.ctx, "session", {}).get("account_info")
    if isinstance(account, dict):
        return account.get("username") or account.get("name") or account.get("id")
    return (
        getattr(account, "username", None)
        or getattr(account, "name", None)
        or getattr(account, "id", None)
    )


def tenant_id(request) -> str:
    value = getattr(request.ctx, "session", {}).get("tenant_id")
    if not value:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return value


def _looks_like_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _model_ref_filter(value: str) -> dict:
    return {"id": value} if _looks_like_uuid(value) else {"key": value}


def _model_ref_list_filter(values: Iterable[str]) -> dict:
    refs = list(values)
    if refs and all(_looks_like_uuid(value) for value in refs):
        return {"id__in": refs}
    return {"key__in": refs}


def _asset_model_id(asset) -> str:
    return getattr(asset, "asset_model_id", None) or getattr(asset, "asset_model_key")


def _field_model_id(field) -> str:
    return getattr(field, "asset_model_id", None) or getattr(field, "asset_model_key")


def _field_ref_model_id(field):
    return getattr(field, "referenced_asset_model_id", None) or getattr(
        field, "referenced_asset_model_key", None
    )


async def get_asset_model_or_404(tenant: str, key_or_id: str) -> AssetModel:
    asset_model = await AssetModel.filter(
        Q(id=key_or_id) | Q(key=key_or_id), tenant_id=tenant, deleted=False
    ).first()
    if not asset_model:
        raise CustomError(ErrorCode.NOT_FOUND)
    return asset_model


async def get_asset_instance_or_404(tenant: str, asset_id: str) -> AssetInstance:
    asset = await AssetInstance.filter(
        id=asset_id, tenant_id=tenant, deleted=False
    ).first()
    if not asset:
        raise CustomError(ErrorCode.NOT_FOUND)
    return asset


async def fields_for_model(
    tenant: str, asset_model_id_or_key: str
) -> list[AssetFieldDefinition]:
    asset_model = await get_asset_model_or_404(tenant, asset_model_id_or_key)
    return (
        await AssetFieldDefinition.filter(
            tenant_id=tenant,
            asset_model_id=asset_model.id,
            deleted=False,
        )
        .order_by("sort", "ctime")
        .all()
    )


def enum_values(field: AssetFieldDefinition) -> set:
    values = set()
    enum_options = (
        field.get("enum_options", []) if isinstance(field, dict) else field.enum_options
    )
    for option in enum_options or []:
        if isinstance(option, dict):
            value = option.get("value")
        else:
            value = option
        if value is not None:
            values.add(value)
    return values


def field_attr(field, name: str, default=None):
    if isinstance(field, dict):
        return field.get(name, default)
    return getattr(field, name, default)


def serialize_asset_field(field: AssetFieldDefinition, primary_field: str) -> dict:
    payload = dict(field.json)
    payload["is_primary"] = field.field_key == primary_field
    return payload


async def validate_field_value(tenant: str, field, value, connection=None):
    data_type = field_attr(field, "data_type")
    options = field_attr(field, "options", {}) or {}
    empty_collection = data_type in {"attachment", "ref"} and value == []
    if value in (None, "") or empty_collection:
        if field_attr(field, "required", False):
            raise CustomError(ErrorCode.PARAM_ERROR)
        return
    if data_type == "text" and not isinstance(value, str):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "number" and (
        isinstance(value, bool) or not isinstance(value, (int, float))
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "bool" and not isinstance(value, bool):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type in {"date", "datetime"} and not isinstance(value, str):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "attachment" and (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "organization":
        if not isinstance(value, str) or value != tenant:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if not await Tenant.filter(id=value, deleted=False).exists():
            raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "user":
        if not isinstance(value, str) or not value.strip():
            raise CustomError(ErrorCode.PARAM_ERROR)
        if not await Account.filter(
            id=value,
            deleted=False,
            disabled=False,
            member__id__in=[tenant],
        ).exists():
            raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "tag" and (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "enum" and isinstance(options, dict) and options.get("multiple"):
        if not isinstance(value, list) or any(
            enum_values(field) and item not in enum_values(field) for item in value
        ):
            raise CustomError(ErrorCode.PARAM_ERROR)
    elif data_type == "enum" and enum_values(field) and value not in enum_values(field):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "ref":
        referenced_asset_model_id = field_attr(field, "referenced_asset_model_id")
        if not referenced_asset_model_id:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if options.get("multiple"):
            if (
                not isinstance(value, list)
                or any(not isinstance(item, str) or not item for item in value)
                or len(value) != len(set(value))
            ):
                raise CustomError(ErrorCode.PARAM_ERROR)
            target_query = AssetInstance.filter(
                id__in=value,
                tenant_id=tenant,
                asset_model_id=referenced_asset_model_id,
                deleted=False,
            )
            if connection is not None:
                target_query = (
                    target_query.using_db(connection).order_by("id").select_for_update()
                )
            targets = await target_query.all()
            if len(targets) != len(value):
                raise CustomError(ErrorCode.PARAM_ERROR)
        else:
            if not isinstance(value, str) or not value:
                raise CustomError(ErrorCode.PARAM_ERROR)
            target_query = AssetInstance.filter(
                id=value,
                tenant_id=tenant,
                asset_model_id=referenced_asset_model_id,
                deleted=False,
            )
            if connection is not None:
                target_query = target_query.using_db(connection).select_for_update()
            target = await target_query.first()
            if not target:
                raise CustomError(ErrorCode.PARAM_ERROR)


async def validate_dynamic_attributes(
    tenant: str, field_defs: list, attributes: dict, connection=None
) -> None:
    field_by_key = {}
    for field in field_defs:
        field_key = (
            field.field_key if hasattr(field, "field_key") else field.get("field_key")
        )
        if not field_key:
            raise CustomError(ErrorCode.PARAM_ERROR)
        field_by_key[field_key] = field
        default_value = (
            field.default_value
            if hasattr(field, "default_value")
            else field.get("default_value")
        )
        if (
            field_attr(field, "data_type") != "ref"
            and field_key not in attributes
            and default_value is not None
        ):
            attributes[field_key] = default_value
        await validate_field_value(
            tenant,
            field,
            attributes.get(field_key),
            connection,
        )
    if set(attributes.keys()) - set(field_by_key.keys()):
        raise CustomError(ErrorCode.PARAM_ERROR)


async def validate_asset_attributes(
    tenant: str,
    asset_model_id: str,
    attributes: dict,
    current_asset_id: str | None = None,
    connection=None,
) -> list[AssetFieldDefinition]:
    field_defs = await fields_for_model(tenant, asset_model_id)
    await validate_dynamic_attributes(tenant, field_defs, attributes, connection)
    existing_query = AssetInstance.filter(
        tenant_id=tenant, asset_model_id=asset_model_id, deleted=False
    )
    if connection is not None:
        existing_query = existing_query.using_db(connection)
    existing_items = await existing_query.all()
    for field in field_defs:
        value = attributes.get(field.field_key)
        if value in (None, "", []):
            continue
        if field.unique:
            for item in existing_items:
                if current_asset_id and item.id == current_asset_id:
                    continue
                if (item.attributes or {}).get(field.field_key) == value:
                    raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
        options = field_attr(field, "options", {}) or {}
        if field.data_type != "ref" or not options.get("reverse_unique"):
            continue
        selected = set(value if options.get("multiple") else [value])
        for item in existing_items:
            if current_asset_id and item.id == current_asset_id:
                continue
            existing_value = (item.attributes or {}).get(field.field_key)
            existing_values = set(
                existing_value
                if options.get("multiple") and isinstance(existing_value, list)
                else [existing_value]
                if existing_value not in (None, "")
                else []
            )
            if selected & existing_values:
                raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)

    rules = await AssetUniqueRule.filter(
        tenant_id=tenant,
        asset_model_id=asset_model_id,
        deleted=False,
    ).all()
    for rule in rules:
        field_keys = list(rule.field_keys or [])
        if not field_keys or any(
            attributes.get(key) in (None, "") for key in field_keys
        ):
            continue
        identity = tuple(attributes.get(key) for key in field_keys)
        for item in existing_items:
            if current_asset_id and item.id == current_asset_id:
                continue
            item_attributes = item.attributes or {}
            if tuple(item_attributes.get(key) for key in field_keys) == identity:
                raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
    return field_defs


def reference_target_ids(field, attributes: dict) -> list[str]:
    if field_attr(field, "data_type") != "ref":
        return []
    field_key = field_attr(field, "field_key")
    value = attributes.get(field_key)
    if value in (None, "", []):
        return []
    options = field_attr(field, "options", {}) or {}
    return list(value) if options.get("multiple") else [value]


async def reference_labels_for_assets(
    tenant: str,
    assets: list[AssetInstance],
    field_defs: list[AssetFieldDefinition] | None = None,
) -> dict[str, dict[str, str | list[str]]]:
    """Resolve reference labels for a page with one fields and one target query."""
    if not assets:
        return {}
    if field_defs is None:
        asset_model_ids = sorted({asset.asset_model_id for asset in assets})
        reference_fields = await (
            AssetFieldDefinition.filter(
                tenant_id=tenant,
                asset_model_id__in=asset_model_ids,
                data_type="ref",
                deleted=False,
            )
            .order_by("sort", "ctime")
            .all()
        )
        fields_by_type = {
            asset_model_id: [
                field
                for field in reference_fields
                if field.asset_model_id == asset_model_id
            ]
            for asset_model_id in asset_model_ids
        }
    else:
        reference_fields = [
            field for field in field_defs if field_attr(field, "data_type") == "ref"
        ]
        fields_by_type = None

    def fields_for_asset(asset: AssetInstance) -> list[AssetFieldDefinition]:
        if fields_by_type is None:
            return reference_fields
        return fields_by_type.get(asset.asset_model_id, [])

    target_ids = {
        target_id
        for asset in assets
        for field in fields_for_asset(asset)
        for target_id in reference_target_ids(field, asset.attributes or {})
    }
    targets = (
        await AssetInstance.filter(
            tenant_id=tenant,
            id__in=sorted(target_ids),
            deleted=False,
        ).all()
        if target_ids
        else []
    )
    names = {str(target.id): target.name for target in targets}
    labels_by_asset: dict[str, dict[str, str | list[str]]] = {}
    for asset in assets:
        labels: dict[str, str | list[str]] = {}
        for field in fields_for_asset(asset):
            ids = reference_target_ids(field, asset.attributes or {})
            if not ids:
                continue
            resolved = [names.get(str(target_id), str(target_id)) for target_id in ids]
            labels[field_attr(field, "field_key")] = (
                resolved
                if (field_attr(field, "options", {}) or {}).get("multiple")
                else resolved[0]
            )
        labels_by_asset[str(asset.id)] = labels
    return labels_by_asset


async def reference_labels_for_asset(
    tenant: str,
    asset: AssetInstance,
    field_defs: list[AssetFieldDefinition] | None = None,
) -> dict[str, str | list[str]]:
    """Resolve one instance's reference IDs to their materialized names."""
    labels_by_asset = await reference_labels_for_assets(tenant, [asset], field_defs)
    return labels_by_asset.get(str(asset.id), {})


async def sync_asset_references(
    tenant: str,
    asset: AssetInstance,
    field_defs: list[AssetFieldDefinition],
    connection,
) -> None:
    await (
        AssetReference.filter(tenant_id=tenant, source_asset_id=asset.id)
        .using_db(connection)
        .delete()
    )
    references = [
        AssetReference(
            tenant_id=tenant,
            source_asset_id=asset.id,
            target_asset_id=target_id,
            field_key=field.field_key,
            position=position,
        )
        for field in field_defs
        for position, target_id in enumerate(
            reference_target_ids(field, asset.attributes or {})
        )
    ]
    if references:
        await AssetReference.bulk_create(
            references,
            using_db=connection,
        )


def payload_asset_model(data: dict, operator: str | None) -> dict:
    data = require_dict(data)
    key = validate_key(data.get("key")) if data.get("key") else None
    status = data.get("status") or "active"
    raw_sort = data.get("sort", 0)
    if raw_sort is None or raw_sort == "":
        raw_sort = 0
    try:
        if isinstance(raw_sort, bool) or (
            isinstance(raw_sort, float) and not raw_sort.is_integer()
        ):
            raise ValueError
        sort = int(raw_sort)
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_ERROR) from None
    if status not in ASSET_MODEL_STATUSES or sort < 0:
        raise CustomError(ErrorCode.PARAM_ERROR)
    return {
        "key": key,
        "name": (data.get("name") or key or "").strip(),
        "icon": data.get("icon"),
        "group_id": data.get("group_id"),
        "group_key": data.get("group_key"),
        "primary_field": "",
        "status": status,
        "sort": sort,
        "updater": operator,
    }


def payload_field(data: dict, operator: str | None) -> dict:
    data = require_dict(data)
    asset_model_id = data.get("asset_model_id") or data.get("asset_model_key")
    if not asset_model_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    payload = payload_dynamic_field(data, allow_missing_key=True)
    return {
        "asset_model_id": asset_model_id,
        "updater": operator,
        **payload,
    }


def payload_dynamic_field(data: dict, *, allow_missing_key: bool = False) -> dict:
    data = require_dict(data)
    field_key = (
        validate_key(data.get("field_key"), "field_key")
        if data.get("field_key")
        else None
        if allow_missing_key
        else validate_key(None, "field_key")
    )
    data_type = data.get("data_type")
    if data_type not in FIELD_DATA_TYPES:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "ref" and not (
        data.get("referenced_asset_model_id") or data.get("referenced_asset_model_key")
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)
    options = data.get("options") or {}
    if not isinstance(options, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data_type == "ref":
        for option_key in ("multiple", "reverse_unique"):
            if option_key in options and not isinstance(options[option_key], bool):
                raise CustomError(ErrorCode.PARAM_ERROR)
        options = {
            **options,
            "multiple": bool(options.get("multiple", False)),
            "reverse_unique": bool(options.get("reverse_unique", False)),
        }
    return {
        "field_key": field_key,
        "label": (data.get("label") or field_key).strip(),
        "data_type": data_type,
        "enum_options": data.get("enum_options") or [],
        "options": options,
        "referenced_asset_model_id": data.get("referenced_asset_model_id")
        or data.get("referenced_asset_model_key"),
        "required": bool(data.get("required", False)),
        "unique": bool(data.get("unique", False)),
        "editable": bool(data.get("editable", True)),
        "is_derived": bool(data.get("is_derived", False)),
        "user_prompt": data.get("user_prompt"),
        "default_value": None if data_type == "ref" else data.get("default_value"),
        "sort": int(data.get("sort", 0) or 0),
    }


def _field_key_base(label: str) -> str:
    transliterated = "".join(
        lazy_pinyin(label, errors=lambda characters: list(characters))
    )
    base = re.sub(r"[^a-zA-Z0-9]+", "_", transliterated).strip("_").lower()
    if not base:
        base = "field"
    if not base[0].isalpha():
        base = f"field_{base}"
    return base[:64].rstrip("_") or "field"


def _has_duplicate_field_label(fields: Iterable, label: str, exclude_id=None) -> bool:
    normalized_label = " ".join(label.split()).casefold()
    return any(
        field.id != exclude_id
        and " ".join(str(field.label or "").split()).casefold() == normalized_label
        for field in fields
    )


async def _allocate_field_key(
    tenant: str,
    asset_model_id: str,
    label: str,
    connection,
) -> str:
    base = _field_key_base(label)
    candidate = base
    suffix = 1
    while (
        await AssetFieldDefinition.filter(
            tenant_id=tenant,
            asset_model_id=asset_model_id,
            field_key=candidate,
        )
        .using_db(connection)
        .exists()
    ):
        suffix += 1
        suffix_text = f"_{suffix}"
        candidate = f"{base[: 64 - len(suffix_text)]}{suffix_text}"
    return candidate


def ensure_expected_existence(existing, expected_exists: bool | None) -> None:
    if expected_exists is None or (existing is not None) == expected_exists:
        return
    raise CustomError(
        ErrorCode.DATA_ALREADY_EXISTS if existing is not None else ErrorCode.NOT_FOUND
    )


async def lock_tenant_for_structure_write(tenant: str, connection) -> Tenant:
    tenant_row = (
        await Tenant.filter(id=tenant, deleted=False)
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if not tenant_row:
        raise CustomError(ErrorCode.NOT_FOUND)
    return tenant_row


async def resolve_asset_model_group(
    tenant: str,
    group_ref: str | None,
    connection,
) -> AssetModelGroup:
    query = AssetModelGroup.filter(tenant_id=tenant, deleted=False)
    if group_ref:
        if not isinstance(group_ref, str):
            raise CustomError(ErrorCode.PARAM_ERROR)
        group_filter = Q(key=group_ref)
        if len(group_ref) <= 36:
            group_filter |= Q(id=group_ref)
        query = query.filter(group_filter)
    group = (
        await query.using_db(connection)
        .order_by("sort", "key")
        .select_for_update()
        .first()
    )
    if not group:
        raise CustomError(ErrorCode.NOT_FOUND)
    return group


@asynccontextmanager
async def structure_write_transaction(tenant: str, connection=None):
    """Serialize model-structure writes per tenant.

    A supplied connection is owned by an outer structure transaction which has
    already acquired the tenant row. Reusing it must not start another
    transaction or acquire the tenant lock a second time.
    """
    if connection is not None:
        yield connection
        return
    async with in_transaction() as transaction_connection:
        await lock_tenant_for_structure_write(tenant, transaction_connection)
        yield transaction_connection


async def lock_asset_model_keys_for_structure(
    tenant: str,
    asset_model_keys: Iterable[str],
    connection,
    *,
    include_all: bool = False,
    include_deleted: bool = False,
    require_all: bool = True,
) -> list[AssetModel]:
    keys = {key for key in asset_model_keys if key}
    if not keys and not include_all:
        return []
    filters = {
        "tenant_id": tenant,
    }
    if not include_deleted:
        filters["deleted"] = False
    if not include_all:
        filters.update(_model_ref_list_filter(sorted(keys)))
    asset_models = (
        await AssetModel.filter(**filters)
        .using_db(connection)
        .order_by("key")
        .select_for_update()
        .all()
    )
    resolved_refs = {
        value
        for asset_model in asset_models
        for value in (getattr(asset_model, "id", None), asset_model.key)
        if value
    }
    if require_all and keys - resolved_refs:
        raise CustomError(ErrorCode.NOT_FOUND)
    return asset_models


async def save_asset_model(
    tenant: str,
    data: dict,
    operator: str | None,
    *,
    expected_exists: bool | None = None,
    allow_sort_update: bool = False,
    connection=None,
) -> AssetModel:
    payload = payload_asset_model(data, operator)
    sort_was_provided = allow_sort_update and "sort" in data
    if not data.get("id") and not payload["key"]:
        payload["key"] = f"m_{uuid.uuid4().hex}"
    async with structure_write_transaction(tenant, connection) as connection:
        if data.get("id"):
            query = AssetModel.filter(
                Q(id=data["id"]) | Q(key=data["id"]),
                tenant_id=tenant,
                deleted=False,
            )
        else:
            query = AssetModel.filter(
                tenant_id=tenant,
                key=payload["key"],
            )
        existing = (
            await query.using_db(connection).order_by("key").select_for_update().first()
        )
        if data.get("id") and not existing:
            raise CustomError(ErrorCode.NOT_FOUND)
        if existing and not payload["key"]:
            payload["key"] = existing.key
        if not payload["name"]:
            payload["name"] = existing.name if existing else payload["key"]
        if data.get("id") and existing.key != payload["key"]:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if not data.get("id") and existing and getattr(existing, "deleted", False):
            raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
        ensure_expected_existence(existing, expected_exists)
        if existing:
            requested_group = payload.get("group_id") or payload.get("group_key")
            if requested_group:
                group = await resolve_asset_model_group(
                    tenant, requested_group, connection
                )
                payload["group_id"] = group.id
                payload["group_key"] = group.key
            else:
                payload.pop("group_id", None)
                payload.pop("group_key", None)
            if "status" in data and data.get("status") != existing.status:
                raise CustomError(ErrorCode.FORBIDDEN)
            payload.pop("status", None)
            for key in ("icon",):
                if key not in data:
                    payload.pop(key, None)
            if not sort_was_provided:
                payload.pop("sort", None)
            payload.pop("primary_field", None)
            for key, value in payload.items():
                setattr(existing, key, value)
            await existing.save(
                using_db=connection,
                update_fields=[*payload, "utime"],
            )
            return existing
        group = await resolve_asset_model_group(
            tenant,
            payload.get("group_id") or payload.get("group_key"),
            connection,
        )
        payload["group_id"] = group.id
        payload["group_key"] = group.key
        if not sort_was_provided:
            last_model = (
                await AssetModel.filter(
                    tenant_id=tenant,
                    deleted=False,
                )
                .using_db(connection)
                .order_by("-sort", "-ctime")
                .first()
            )
            payload["sort"] = max(0, int(getattr(last_model, "sort", 0) or 0)) + 1
        return await AssetModel.create(
            using_db=connection,
            tenant_id=tenant,
            creator=operator,
            **payload,
        )


async def reorder_asset_models(
    tenant: str,
    asset_model_ids: list[str] | None,
    operator: str | None,
    *,
    connection=None,
) -> list[AssetModel]:
    if (
        not isinstance(asset_model_ids, list)
        or any(
            not isinstance(asset_model_id, str) or not asset_model_id
            for asset_model_id in asset_model_ids
        )
        or len(asset_model_ids) != len(set(asset_model_ids))
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)

    async with structure_write_transaction(tenant, connection) as connection:
        active_models = (
            await AssetModel.filter(
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .order_by("sort", "key")
            .select_for_update()
            .all()
        )
        models_by_id = {str(model.id): model for model in active_models}
        if len(asset_model_ids) != len(active_models) or set(asset_model_ids) != set(
            models_by_id
        ):
            raise CustomError(ErrorCode.PARAM_ERROR)

        ordered_models = [
            models_by_id[asset_model_id] for asset_model_id in asset_model_ids
        ]
        for position, model in enumerate(ordered_models, start=1):
            if model.sort == position:
                continue
            model.sort = position
            model.updater = operator
            await model.save(
                using_db=connection,
                update_fields=["sort", "updater", "utime"],
            )
    return ordered_models


async def validate_reference_field_instances(
    tenant: str,
    asset_model_id: str,
    field: dict,
    connection,
) -> list[AssetInstance]:
    instances = (
        await AssetInstance.filter(
            tenant_id=tenant,
            asset_model_id=asset_model_id,
            deleted=False,
        )
        .using_db(connection)
        .order_by("id")
        .select_for_update()
        .all()
    )
    seen_targets: set[str] = set()
    seen_values: set[object] = set()
    options = field.get("options") or {}
    for instance in instances:
        value = (instance.attributes or {}).get(field["field_key"])
        await validate_field_value(tenant, field, value, connection)
        if value in (None, "", []):
            continue
        if field.get("unique"):
            identity = tuple(value) if isinstance(value, list) else value
            if identity in seen_values:
                raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
            seen_values.add(identity)
        if options.get("reverse_unique"):
            targets = set(reference_target_ids(field, instance.attributes or {}))
            if seen_targets & targets:
                raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
            seen_targets.update(targets)
    return instances


async def save_field(
    tenant: str,
    data: dict,
    operator: str | None,
    *,
    expected_exists: bool | None = None,
    connection=None,
) -> AssetFieldDefinition:
    payload = payload_field(data, operator)
    payload["label"] = str(payload.get("label") or "").strip()
    if not payload["label"]:
        raise CustomError(ErrorCode.PARAM_ERROR)
    requested_model_ref = payload["asset_model_id"]
    make_primary = data.get("is_primary") is True
    async with structure_write_transaction(tenant, connection) as connection:
        asset_model = (
            await AssetModel.filter(
                tenant_id=tenant,
                deleted=False,
                **_model_ref_filter(payload["asset_model_id"]),
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not asset_model:
            raise CustomError(ErrorCode.NOT_FOUND)
        payload["asset_model_id"] = asset_model.id
        payload["asset_model_key"] = asset_model.key
        if payload.get("referenced_asset_model_id"):
            referenced_asset_model_key = (
                await AssetModel.filter(
                    tenant_id=tenant,
                    deleted=False,
                    **_model_ref_filter(payload["referenced_asset_model_id"]),
                )
                .using_db(connection)
                .first()
            )
            if not referenced_asset_model_key:
                raise CustomError(ErrorCode.NOT_FOUND)
            payload["referenced_asset_model_id"] = referenced_asset_model_key.id
            payload["referenced_asset_model_key"] = referenced_asset_model_key.key
        else:
            payload["referenced_asset_model_key"] = None

        existing = None
        if data.get("id"):
            existing = (
                await AssetFieldDefinition.filter(
                    id=data["id"],
                    tenant_id=tenant,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if not existing:
                raise CustomError(ErrorCode.NOT_FOUND)
            if _field_model_id(existing) not in {
                payload["asset_model_id"],
                asset_model.key,
            }:
                raise CustomError(ErrorCode.PARAM_ERROR)
        if not existing and payload.get("field_key"):
            existing = (
                await AssetFieldDefinition.filter(
                    tenant_id=tenant,
                    field_key=payload["field_key"],
                    deleted=False,
                    **(
                        {"asset_model_id": payload["asset_model_id"]}
                        if _looks_like_uuid(requested_model_ref)
                        else {"asset_model_key": asset_model.key}
                    ),
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
        ensure_expected_existence(existing, expected_exists)
        active_fields = (
            await AssetFieldDefinition.filter(
                tenant_id=tenant,
                asset_model_id=payload["asset_model_id"],
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .all()
        )
        if _has_duplicate_field_label(
            active_fields,
            payload["label"],
            getattr(existing, "id", None),
        ):
            raise CustomError(ErrorCode.ASSET_FIELD_NAME_EXISTS)
        if existing:
            payload["field_key"] = existing.field_key
        elif not payload.get("field_key"):
            payload["field_key"] = await _allocate_field_key(
                tenant,
                payload["asset_model_id"],
                payload["label"],
                connection,
            )
        referenced_asset_model_keys = {
            key
            for key in (
                payload.get("referenced_asset_model_key"),
                getattr(existing, "referenced_asset_model_key", None),
            )
            if key and key != asset_model.key
        }
        await lock_asset_model_keys_for_structure(
            tenant,
            referenced_asset_model_keys,
            connection,
            include_deleted=True,
            require_all=False,
        )
        existing_was_primary = bool(
            existing and existing.field_key == asset_model.primary_field
        )
        if make_primary and not existing_was_primary and payload["data_type"] != "text":
            raise CustomError(ErrorCode.PARAM_ERROR)
        if existing_was_primary and payload["data_type"] not in {
            "text",
            existing.data_type,
        }:
            raise CustomError(ErrorCode.PARAM_ERROR)

        reference_instances = []
        if existing and (existing.data_type == "ref" or payload["data_type"] == "ref"):
            if payload["data_type"] == "ref":
                try:
                    reference_instances = await validate_reference_field_instances(
                        tenant,
                        asset_model.id,
                        payload,
                        connection,
                    )
                except CustomError as exc:
                    if exc.enum is ErrorCode.PARAM_ERROR:
                        raise CustomError(
                            ErrorCode.ASSET_FIELD_DATA_INCOMPATIBLE
                        ) from exc
                    raise
            else:
                reference_instances = (
                    await AssetInstance.filter(
                        tenant_id=tenant,
                        asset_model_id=asset_model.id,
                        deleted=False,
                    )
                    .using_db(connection)
                    .order_by("id")
                    .select_for_update()
                    .all()
                )

        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
            await existing.save(using_db=connection)
            item = existing
        else:
            item = await AssetFieldDefinition.create(
                using_db=connection,
                tenant_id=tenant,
                creator=operator,
                **payload,
            )

        # `false` never clears the model primary field. Selecting another field replaces
        # it, and renaming the current primary follows the new key to avoid a stale link.
        if (
            make_primary or existing_was_primary
        ) and asset_model.primary_field != item.field_key:
            asset_model.primary_field = item.field_key
            asset_model.updater = operator
            await asset_model.save(
                using_db=connection,
                update_fields=["primary_field", "updater", "utime"],
            )
        if reference_instances:
            current_fields = [
                field for field in active_fields if field.id != item.id
            ] + [item]
            current_fields.sort(key=lambda field: (field.sort, field.ctime))
            for instance in reference_instances:
                await sync_asset_references(
                    tenant,
                    instance,
                    current_fields,
                    connection,
                )
    return item


async def reorder_fields(
    tenant: str,
    asset_model_ref: str | None,
    field_ids: list[str] | None,
    operator: str | None,
    *,
    connection=None,
) -> list[AssetFieldDefinition]:
    if (
        not asset_model_ref
        or not isinstance(field_ids, list)
        or any(not isinstance(field_id, str) or not field_id for field_id in field_ids)
        or len(field_ids) != len(set(field_ids))
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)

    async with structure_write_transaction(tenant, connection) as connection:
        asset_model = (
            await AssetModel.filter(
                tenant_id=tenant,
                deleted=False,
                **_model_ref_filter(asset_model_ref),
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not asset_model:
            raise CustomError(ErrorCode.NOT_FOUND)
        active_fields = (
            await AssetFieldDefinition.filter(
                tenant_id=tenant,
                asset_model_id=asset_model.id,
                deleted=False,
            )
            .using_db(connection)
            .order_by("sort", "ctime")
            .select_for_update()
            .all()
        )
        fields_by_id = {str(field.id): field for field in active_fields}
        if len(field_ids) != len(active_fields) or set(field_ids) != set(fields_by_id):
            raise CustomError(ErrorCode.PARAM_ERROR)

        ordered_fields = [fields_by_id[field_id] for field_id in field_ids]
        for position, field in enumerate(ordered_fields, start=1):
            if field.sort == position:
                continue
            field.sort = position
            field.updater = operator
            await field.save(
                using_db=connection,
                update_fields=["sort", "updater", "utime"],
            )
    return ordered_fields


async def delete_field(
    tenant: str,
    field_id: str,
    *,
    connection=None,
) -> dict:
    async with structure_write_transaction(tenant, connection) as connection:
        candidate = (
            await AssetFieldDefinition.filter(
                id=field_id,
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .first()
        )
        if not candidate:
            raise CustomError(ErrorCode.NOT_FOUND)
        asset_model = (
            await AssetModel.filter(
                tenant_id=tenant,
                **_model_ref_filter(_field_model_id(candidate)),
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not asset_model:
            raise CustomError(ErrorCode.NOT_FOUND)
        referenced_asset_model_keys = {_field_ref_model_id(candidate)} - {
            None,
            asset_model.id,
        }
        await lock_asset_model_keys_for_structure(
            tenant,
            referenced_asset_model_keys,
            connection,
            include_deleted=True,
            require_all=False,
        )
        field = (
            await AssetFieldDefinition.filter(
                id=field_id,
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not field:
            raise CustomError(ErrorCode.NOT_FOUND)
        if _field_model_id(field) not in {asset_model.id, asset_model.key}:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if field.field_key == asset_model.primary_field:
            raise CustomError(ErrorCode.FORBIDDEN)
        source_asset_ids = await (
            AssetInstance.filter(tenant_id=tenant, asset_model_id=asset_model.id)
            .using_db(connection)
            .values_list("id", flat=True)
        )
        if source_asset_ids:
            await (
                AssetReference.filter(
                    tenant_id=tenant,
                    source_asset_id__in=list(source_asset_ids),
                    field_key=field.field_key,
                )
                .using_db(connection)
                .delete()
            )
        field.deleted = True
        await field.save(using_db=connection, update_fields=["deleted", "utime"])
    return {"id": field.id}


async def set_package_primary_field(
    tenant: str,
    asset_model_key: str,
    primary_field: str | None,
    operator: str | None,
    *,
    connection=None,
) -> AssetModel:
    asset_model_key = validate_key(asset_model_key, "asset_model_key")
    primary_field = (
        validate_key(primary_field, "primary_field") if primary_field else ""
    )
    async with structure_write_transaction(tenant, connection) as connection:
        asset_model = (
            await AssetModel.filter(
                tenant_id=tenant,
                key=asset_model_key,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not asset_model:
            raise CustomError(ErrorCode.NOT_FOUND)
        if asset_model.primary_field == primary_field:
            return asset_model
        if primary_field:
            field = (
                await AssetFieldDefinition.filter(
                    tenant_id=tenant,
                    asset_model_key=asset_model_key,
                    field_key=primary_field,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if not field or field.data_type != "text":
                raise CustomError(ErrorCode.PARAM_ERROR)
        if asset_model.primary_field != primary_field:
            asset_model.primary_field = primary_field
            asset_model.updater = operator
            await asset_model.save(
                using_db=connection,
                update_fields=["primary_field", "updater", "utime"],
            )
    return asset_model


async def _save_asset_instance_for_type(
    tenant: str,
    data: dict,
    operator: str | None,
    asset_model: AssetModel,
    existing: AssetInstance | None = None,
    *,
    connection=None,
) -> AssetInstance:
    if asset_model.status != "active":
        raise CustomError(ErrorCode.FORBIDDEN)
    asset_model_key = asset_model.key
    attributes = data.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    field_defs = await validate_asset_attributes(
        tenant,
        asset_model.id,
        attributes,
        getattr(existing, "id", None),
        connection,
    )
    status = data.get("status") or getattr(existing, "status", "active")
    risk_status = data.get("risk_status") or getattr(existing, "risk_status", "normal")
    if status not in ASSET_STATUSES or risk_status not in RISK_STATUSES:
        raise CustomError(ErrorCode.PARAM_ERROR)
    name_value = data.get("name")
    if name_value in (None, "") and asset_model.primary_field:
        name_value = attributes.get(asset_model.primary_field)
    if name_value in (None, ""):
        name_value = asset_model_key
    name = str(name_value).strip() or asset_model_key
    payload = {
        "asset_model_id": asset_model.id,
        "asset_model_key": asset_model_key,
        "name": name,
        "project_area_id": data.get("project_area_id"),
        "accessory_project_id": data.get("accessory_project_id"),
        "attributes": attributes,
        "status": status,
        "risk_status": risk_status,
        "updater": operator,
    }
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        if connection is None:
            await existing.save()
        else:
            await existing.save(using_db=connection)
        await sync_asset_references(tenant, existing, field_defs, connection)
        return existing
    created = await AssetInstance.create(
        using_db=connection,
        tenant_id=tenant,
        creator=operator,
        **payload,
    )
    await sync_asset_references(tenant, created, field_defs, connection)
    return created


async def save_asset_instance(
    tenant: str, data: dict, operator: str | None
) -> AssetInstance:
    data = require_dict(data)
    asset_model_id = data.get("asset_model_id") or data.get("asset_model_key")
    if not asset_model_id:
        raise CustomError(ErrorCode.PARAM_ERROR)
    if data.get("id"):
        async with in_transaction() as connection:
            asset_model = (
                await AssetModel.filter(
                    tenant_id=tenant,
                    deleted=False,
                    **_model_ref_filter(asset_model_id),
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if not asset_model:
                raise CustomError(ErrorCode.NOT_FOUND)
            existing = (
                await AssetInstance.filter(
                    id=data["id"],
                    tenant_id=tenant,
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if not existing:
                raise CustomError(ErrorCode.NOT_FOUND)
            if _asset_model_id(existing) not in {asset_model.id, asset_model.key}:
                raise CustomError(ErrorCode.PARAM_ERROR)
            return await _save_asset_instance_for_type(
                tenant,
                data,
                operator,
                asset_model,
                existing,
                connection=connection,
            )

    # Model deletion takes the same row lock before checking for instances.
    # The lock is model-scoped so unrelated 资产 creates remain concurrent.
    async with in_transaction() as connection:
        asset_model = (
            await AssetModel.filter(
                tenant_id=tenant,
                deleted=False,
                **_model_ref_filter(asset_model_id),
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not asset_model:
            raise CustomError(ErrorCode.NOT_FOUND)
        return await _save_asset_instance_for_type(
            tenant,
            data,
            operator,
            asset_model,
            connection=connection,
        )


async def delete_asset_instance(
    tenant: str,
    asset_id: str,
    *,
    connection=None,
) -> dict:
    """Hard-delete an unreferenced asset instance and its outgoing index rows."""
    async with structure_write_transaction(tenant, connection) as connection:
        asset = (
            await AssetInstance.filter(
                id=asset_id,
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not asset:
            raise CustomError(ErrorCode.NOT_FOUND)
        asset_model = (
            await AssetModel.filter(
                tenant_id=tenant,
                id=asset.asset_model_id,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not asset_model:
            raise CustomError(ErrorCode.NOT_FOUND)
        if asset_model.status != "active":
            raise CustomError(ErrorCode.FORBIDDEN)
        has_incoming_references = await (
            AssetReference.filter(
                tenant_id=tenant,
                target_asset_id=asset.id,
                deleted=False,
            )
            .using_db(connection)
            .exists()
        )
        if has_incoming_references:
            raise CustomError(ErrorCode.ASSET_INSTANCE_HAS_REFERENCES)
        await (
            AssetReference.filter(
                source_asset_id=asset.id,
                tenant_id=tenant,
            )
            .using_db(connection)
            .delete()
        )
        await asset.delete(using_db=connection)
        return {"id": asset.id}


async def delete_asset_model(
    tenant: str,
    key_or_id: str,
    *,
    connection=None,
) -> dict:
    async with structure_write_transaction(tenant, connection) as connection:
        asset_model = (
            await AssetModel.filter(
                Q(id=key_or_id) | Q(key=key_or_id),
                tenant_id=tenant,
                deleted=False,
            )
            .using_db(connection)
            .order_by("key")
            .select_for_update()
            .first()
        )
        if not asset_model:
            raise CustomError(ErrorCode.NOT_FOUND)
        # Only live instances block model deletion. Any historical soft-delete
        # tombstones left by older builds are purged below so they neither block
        # nor leave orphan rows after the model is hard-deleted.
        active_asset = (
            await AssetInstance.filter(
                tenant_id=tenant,
                asset_model_id=asset_model.id,
                deleted=False,
            )
            .using_db(connection)
            .order_by("id")
            .select_for_update()
            .first()
        )
        if active_asset:
            raise CustomError(ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES)
        referencing_fields = (
            await AssetFieldDefinition.filter(
                tenant_id=tenant,
                deleted=False,
                referenced_asset_model_id=asset_model.id,
            )
            .using_db(connection)
            .order_by("asset_model_key", "field_key")
            .select_for_update()
            .all()
        )
        if any(
            _field_model_id(field) not in {asset_model.id, asset_model.key}
            for field in referencing_fields
        ):
            raise CustomError(ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES)
        # Deleted field definitions remain as tombstones. Detach their inbound
        # references before the target model is hard-deleted so the FK can be
        # released without discarding the field history.
        await (
            AssetFieldDefinition.filter(
                tenant_id=tenant,
                deleted=True,
                referenced_asset_model_id=asset_model.id,
            )
            .using_db(connection)
            .update(
                referenced_asset_model_id=None,
                referenced_asset_model_key=None,
            )
        )
        # Empty models have no historical identity to retain. Purge leftover
        # soft-deleted instances and derived reference indexes first, then
        # remove field definitions and the model itself.
        asset_ids = list(
            await AssetInstance.filter(
                tenant_id=tenant,
                asset_model_id=asset_model.id,
            )
            .using_db(connection)
            .values_list("id", flat=True)
        )
        if asset_ids:
            await (
                AssetReference.filter(
                    Q(source_asset_id__in=asset_ids) | Q(target_asset_id__in=asset_ids),
                    tenant_id=tenant,
                )
                .using_db(connection)
                .delete()
            )
        await (
            AssetInstance.filter(
                tenant_id=tenant,
                asset_model_id=asset_model.id,
            )
            .using_db(connection)
            .delete()
        )
        await (
            AssetFieldDefinition.filter(
                tenant_id=tenant,
                asset_model_id=asset_model.id,
            )
            .using_db(connection)
            .delete()
        )
        await asset_model.delete(using_db=connection)
        return {"id": asset_model.id}


async def asset_directory_options(tenant: str) -> dict:
    """Return stable IDs for the asset organization and user field editors."""
    tenant_row = (
        await Tenant.filter(id=tenant, deleted=False).prefetch_related("member").first()
    )
    if not tenant_row:
        raise CustomError(ErrorCode.NOT_FOUND)
    members = sorted(
        (
            account
            for account in tenant_row.member
            if not account.deleted and not account.disabled
        ),
        key=lambda account: (
            str(account.display_name or account.username).casefold(),
            account.username.casefold(),
        ),
    )
    organization_name = tenant_row.name or tenant_row.id
    return {
        "organizations": [
            {
                "id": tenant_row.id,
                "name": organization_name,
                "path": organization_name,
            }
        ],
        "users": [
            {
                "id": account.id,
                "name": account.display_name or account.username,
                "username": account.username,
            }
            for account in members
        ],
    }


async def export_asset_model_package(
    tenant: str,
    asset_model_keys: Iterable[str] | None = None,
    include_related: bool = False,
) -> dict:
    requested_refs = list(dict.fromkeys(asset_model_keys or []))
    requested_models = []
    if requested_refs:
        requested_models = await AssetModel.filter(
            Q(id__in=requested_refs) | Q(key__in=requested_refs),
            tenant_id=tenant,
            deleted=False,
        ).all()
    requested_keys = [model.key for model in requested_models]
    keys = requested_keys[:]
    if requested_refs and include_related:
        all_models = await AssetModel.filter(tenant_id=tenant, deleted=False).all()
        model_key_by_ref = {
            ref: model.key for model in all_models for ref in (model.id, model.key)
        }
        reference_fields = await AssetFieldDefinition.filter(
            tenant_id=tenant,
            data_type="ref",
            deleted=False,
        ).all()
        related_keys = set(requested_keys)
        changed = True
        while changed:
            changed = False
            for field in reference_fields:
                source_key = model_key_by_ref.get(_field_model_id(field))
                target_key = model_key_by_ref.get(_field_ref_model_id(field))
                if not source_key or not target_key:
                    continue
                if source_key in related_keys or target_key in related_keys:
                    before = len(related_keys)
                    related_keys.update((source_key, target_key))
                    changed = changed or len(related_keys) != before
        keys = sorted(related_keys)

    condition = Q(tenant_id=tenant, deleted=False)
    if keys:
        condition &= Q(key__in=keys)
    asset_models = await AssetModel.filter(condition).order_by("sort", "key").all()
    keys = [item.key for item in asset_models]
    fields = (
        await AssetFieldDefinition.filter(
            tenant_id=tenant, asset_model_key__in=keys, deleted=False
        )
        .order_by("asset_model_key", "sort")
        .all()
    )
    groups = (
        await AssetModelGroup.filter(
            tenant_id=tenant,
            key__in=sorted({item.group_key for item in asset_models}),
            deleted=False,
        )
        .order_by("sort", "key")
        .all()
    )
    unique_rules = (
        await AssetUniqueRule.filter(
            tenant_id=tenant,
            asset_model_id__in=[item.id for item in asset_models],
            deleted=False,
        )
        .order_by("asset_model_key", "sort", "key")
        .all()
    )
    return {
        "schema_version": 2,
        "asset_model_groups": [_portable_package_item(item.json) for item in groups],
        "asset_models": [_portable_package_item(item.json) for item in asset_models],
        "asset_fields": [_portable_package_item(item.json) for item in fields],
        "asset_unique_rules": [
            _portable_package_item(item.json) for item in unique_rules
        ],
    }


MODEL_PACKAGE_IDENTITY_FIELDS = {
    "id",
    "tenant_id",
    "ctime",
    "utime",
    "creator",
    "updater",
    "deleted",
    # Runtime model UUIDs are local to one installation. Portable packages
    # keep using their hidden key references and remap them on import.
    "asset_model_id",
    "referenced_asset_model_id",
    "group_id",
    "is_locked",
    "is_builtin",
    "builtin_key",
    "builtin_version",
    "builtin_hash",
    "owner",
    "field_group",
    "field_group_sort",
}

MODEL_PACKAGE_ITEM_FIELDS = {
    "asset_model_groups": {"key", "name", "sort"},
    "asset_models": {
        "key",
        "name",
        "icon",
        "group_key",
        "primary_field",
        "status",
        "sort",
    },
    "asset_fields": {
        "asset_model_key",
        "field_key",
        "source_field_key",
        "label",
        "data_type",
        "enum_options",
        "options",
        "referenced_asset_model_key",
        "required",
        "unique",
        "editable",
        "is_derived",
        "user_prompt",
        "default_value",
        "sort",
        "is_primary",
    },
    "asset_unique_rules": {"asset_model_key", "key", "field_keys", "sort"},
}


def _portable_package_item(item: dict) -> dict:
    portable = {
        key: value
        for key, value in item.items()
        if key not in MODEL_PACKAGE_IDENTITY_FIELDS
    }
    return portable


def normalize_asset_model_package(package: dict) -> dict:
    package = require_dict(package)
    package_keys = {
        "schema_version",
        "asset_model_groups",
        "asset_models",
        "asset_fields",
        "asset_unique_rules",
    }
    if package.get("schema_version") != 2 or set(package) != package_keys:
        raise CustomError(ErrorCode.PARAM_ERROR)

    def normalized_items(key: str, excluded: set[str] | None = None) -> list[dict]:
        source = package[key]
        if not isinstance(source, list):
            raise CustomError(ErrorCode.PARAM_ERROR)
        excluded_fields = MODEL_PACKAGE_IDENTITY_FIELDS | (excluded or set())
        allowed_fields = MODEL_PACKAGE_ITEM_FIELDS[key] | excluded_fields
        items = []
        for item in source:
            if not isinstance(item, dict) or set(item) - allowed_fields:
                raise CustomError(ErrorCode.PARAM_ERROR)
            items.append(
                {
                    key: value
                    for key, value in item.items()
                    if key not in excluded_fields
                }
            )
        return items

    model_groups = normalized_items("asset_model_groups")
    for item in model_groups:
        item["key"] = validate_key(item.get("key"), "group_key")

    asset_models = normalized_items("asset_models", excluded={"status"})
    for item in asset_models:
        item["key"] = validate_key(item.get("key"))
        item["group_key"] = validate_key(item.get("group_key"), "group_key")
        if "primary_field" in item:
            item["primary_field"] = (
                validate_key(item.get("primary_field"), "primary_field")
                if item.get("primary_field")
                else ""
            )

    fields = normalized_items("asset_fields", excluded={"is_primary"})
    for item in fields:
        item["asset_model_key"] = validate_key(
            item.get("asset_model_key"), "asset_model_key"
        )
        item["field_key"] = validate_key(item.get("field_key"), "field_key")

    unique_rules = normalized_items("asset_unique_rules")
    for item in unique_rules:
        item["asset_model_key"] = validate_key(
            item.get("asset_model_key"), "asset_model_key"
        )
        item["key"] = validate_key(item.get("key"), "unique_rule_key")
        field_keys = item.get("field_keys")
        if not isinstance(field_keys, list) or not field_keys:
            raise CustomError(ErrorCode.PARAM_ERROR)
        item["field_keys"] = [
            validate_key(field_key, "field_key") for field_key in field_keys
        ]

    return {
        "schema_version": 2,
        "asset_model_groups": model_groups,
        "asset_models": asset_models,
        "asset_fields": fields,
        "asset_unique_rules": unique_rules,
    }


async def import_asset_model_package(
    tenant: str,
    package: dict,
    operator: str | None,
    expected_existing: dict[str, set] | None = None,
) -> dict:
    async with structure_write_transaction(tenant) as connection:
        return await _import_asset_model_package_locked(
            tenant,
            package,
            operator,
            expected_existing,
            connection=connection,
        )


async def _import_asset_model_package_locked(
    tenant: str,
    package: dict,
    operator: str | None,
    expected_existing: dict[str, set] | None = None,
    *,
    connection,
) -> dict:
    created_or_updated = {
        "asset_model_groups": 0,
        "asset_models": 0,
        "asset_fields": 0,
        "asset_unique_rules": 0,
    }
    normalized_package = normalize_asset_model_package(package)
    model_groups = normalized_package["asset_model_groups"]
    asset_models = normalized_package["asset_models"]
    fields = normalized_package["asset_fields"]
    unique_rules = normalized_package["asset_unique_rules"]

    def expected(resource: str, identity) -> bool | None:
        if expected_existing is None:
            return None
        return identity in expected_existing.get(resource, set())

    for item in model_groups:
        existing_group = (
            await AssetModelGroup.filter(
                tenant_id=tenant,
                key=item["key"],
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        ensure_expected_existence(
            existing_group,
            expected("asset_model_groups", item["key"]),
        )
        if existing_group:
            existing_group.name = str(item.get("name") or item["key"]).strip()
            existing_group.sort = int(item.get("sort", existing_group.sort) or 0)
            existing_group.updater = operator
            await existing_group.save(
                using_db=connection,
                update_fields=["name", "sort", "updater", "utime"],
            )
        else:
            await AssetModelGroup.create(
                using_db=connection,
                tenant_id=tenant,
                key=item["key"],
                name=str(item.get("name") or item["key"]).strip(),
                sort=int(item.get("sort", 10000) or 0),
                is_builtin=False,
                creator=operator,
                updater=operator,
            )
        created_or_updated["asset_model_groups"] += 1

    packaged_fields = {
        (
            validate_key(item.get("asset_model_key"), "asset_model_key"),
            validate_key(item.get("field_key"), "field_key"),
        ): item
        for item in fields
    }
    for item in asset_models:
        primary_field = item.get("primary_field")
        if not primary_field:
            continue
        asset_model_key = validate_key(item.get("key"))
        primary_field = validate_key(primary_field, "primary_field")
        packaged_primary = packaged_fields.get((asset_model_key, primary_field))
        if packaged_primary:
            if packaged_primary.get("data_type") != "text":
                raise CustomError(ErrorCode.PARAM_ERROR)
            continue
        existing_field = (
            await AssetFieldDefinition.filter(
                tenant_id=tenant,
                asset_model_key=asset_model_key,
                field_key=primary_field,
                deleted=False,
            )
            .using_db(connection)
            .first()
        )
        if not existing_field or existing_field.data_type != "text":
            raise CustomError(ErrorCode.PARAM_ERROR)

    for item in asset_models:
        await save_asset_model(
            tenant,
            item,
            operator,
            expected_exists=expected("asset_models", item["key"]),
            allow_sort_update=True,
            connection=connection,
        )
        created_or_updated["asset_models"] += 1

    pending_fields = {
        (item.get("asset_model_key"), item.get("field_key")): item for item in fields
    }
    for item in asset_models:
        if "primary_field" not in item:
            continue
        asset_model_key = item.get("key")
        primary_field = item.get("primary_field")
        primary_item = pending_fields.pop((asset_model_key, primary_field), None)
        if primary_item:
            await save_field(
                tenant,
                primary_item,
                operator,
                expected_exists=expected(
                    "asset_fields",
                    (primary_item["asset_model_key"], primary_item["field_key"]),
                ),
                connection=connection,
            )
            created_or_updated["asset_fields"] += 1
        await set_package_primary_field(
            tenant,
            asset_model_key,
            primary_field,
            operator,
            connection=connection,
        )

    for item in pending_fields.values():
        await save_field(
            tenant,
            item,
            operator,
            expected_exists=expected(
                "asset_fields",
                (item["asset_model_key"], item["field_key"]),
            ),
            connection=connection,
        )
        created_or_updated["asset_fields"] += 1
    for item in unique_rules:
        asset_model = (
            await AssetModel.filter(
                tenant_id=tenant,
                key=item["asset_model_key"],
                deleted=False,
            )
            .using_db(connection)
            .first()
        )
        if not asset_model:
            raise CustomError(ErrorCode.NOT_FOUND)
        available_fields = set(
            await AssetFieldDefinition.filter(
                tenant_id=tenant,
                asset_model_id=asset_model.id,
                deleted=False,
            )
            .using_db(connection)
            .values_list("field_key", flat=True)
        )
        if set(item["field_keys"]) - available_fields:
            raise CustomError(ErrorCode.PARAM_ERROR)
        existing_rule = (
            await AssetUniqueRule.filter(
                tenant_id=tenant,
                asset_model_id=asset_model.id,
                key=item["key"],
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        ensure_expected_existence(
            existing_rule,
            expected("asset_unique_rules", (item["asset_model_key"], item["key"])),
        )
        values = {
            "asset_model_id": asset_model.id,
            "asset_model_key": asset_model.key,
            "field_keys": item["field_keys"],
            "sort": int(item.get("sort", 0) or 0),
            "updater": operator,
        }
        if existing_rule:
            for key, value in values.items():
                setattr(existing_rule, key, value)
            await existing_rule.save(using_db=connection)
        else:
            await AssetUniqueRule.create(
                using_db=connection,
                tenant_id=tenant,
                key=item["key"],
                creator=operator,
                **values,
            )
        created_or_updated["asset_unique_rules"] += 1
    return created_or_updated


async def build_reference_graph(
    tenant: str, root_asset_id: str | None = None, depth: int = 1
) -> dict:
    depth = max(1, min(3, int(depth or 1)))
    visited: dict[str, AssetInstance] = {}
    edges: dict[str, AssetReference] = {}
    frontier = {root_asset_id} if root_asset_id else set()
    if not frontier:
        roots = (
            await AssetInstance.filter(tenant_id=tenant, deleted=False)
            .order_by("-utime")
            .limit(50)
            .all()
        )
        visited.update({item.id: item for item in roots})
        frontier = {item.id for item in roots}
    for _ in range(depth):
        if not frontier:
            break
        related = await AssetReference.filter(
            Q(source_asset_id__in=list(frontier))
            | Q(target_asset_id__in=list(frontier)),
            tenant_id=tenant,
            deleted=False,
        ).all()
        next_frontier = set()
        edges.update({item.id: item for item in related})
        for reference in related:
            next_frontier.add(reference.source_asset_id)
            next_frontier.add(reference.target_asset_id)
        frontier = next_frontier - set(visited.keys())
        assets = await AssetInstance.filter(
            id__in=list(next_frontier), tenant_id=tenant, deleted=False
        ).all()
        visited.update({item.id: item for item in assets})
    if root_asset_id and root_asset_id not in visited:
        root = await get_asset_instance_or_404(tenant, root_asset_id)
        visited[root.id] = root
    source_models = {_asset_model_id(item) for item in visited.values()}
    field_defs = await AssetFieldDefinition.filter(
        Q(asset_model_id__in=sorted(source_models))
        | Q(asset_model_key__in=sorted(source_models)),
        tenant_id=tenant,
        data_type="ref",
        deleted=False,
    ).all()
    field_labels = {
        (model_ref, field.field_key): field.label
        for field in field_defs
        for model_ref in (field.asset_model_id, field.asset_model_key)
    }
    return {
        "nodes": [
            {
                "id": item.id,
                "text": item.name,
                "type": item.asset_model_key,
                "properties": item.json,
            }
            for item in visited.values()
        ],
        "edges": [
            {
                "id": item.id,
                "sourceNodeId": item.source_asset_id,
                "targetNodeId": item.target_asset_id,
                "text": field_labels.get(
                    (_asset_model_id(visited[item.source_asset_id]), item.field_key),
                    item.field_key,
                ),
                "properties": item.json,
            }
            for item in edges.values()
        ],
    }
