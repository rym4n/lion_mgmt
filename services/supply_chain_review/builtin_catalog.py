# coding=utf-8
from __future__ import annotations

import datetime
import hashlib
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.supply_chain_review import (
    CI_TYPE_STATUS_ACTIVE,
    CI_TYPE_STATUS_DISABLED,
    FIELD_DATA_TYPES,
    SupplyChainBuiltinActivation,
    SupplyChainBuiltinActivationEvent,
    SupplyChainBuiltinActivationPreview,
    SupplyChainBuiltinCatalogEvent,
    SupplyChainBuiltinCatalogState,
    SupplyChainCi,
    SupplyChainCiFieldDef,
    SupplyChainCiModelGroup,
    SupplyChainCiType,
    SupplyChainCiUniqueRule,
)
from models.user import Account, Tenant
from tortoise.transactions import in_transaction


CATALOG_VERSION = "bklite-2026.07-v1"
CATALOG_DIRECTORY = Path(__file__).with_name("builtin_catalog") / CATALOG_VERSION
CATALOG_FILES = (
    "groups",
    "models",
    "fields",
    "unique_rules",
    "icon_aliases",
)
CATALOG_COUNTS = {
    "groups": 30,
    "models": 236,
    "fields": 3433,
    "unique_rules": 1,
    "icon_aliases": 28,
}
DERIVED_FIELD_COUNT = 0
ACTIVATION_ACTIONS = {"enable", "disable"}
ACTIVATION_SCOPES = {"model", "group", "selection", "all"}
GROUP_KEY_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _normalized_field_label(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


@lru_cache(maxsize=1)
def load_builtin_catalog() -> dict:
    metadata = json.loads(
        (CATALOG_DIRECTORY / "catalog.json").read_text(encoding="utf-8")
    )
    catalog = {
        name: json.loads(
            (CATALOG_DIRECTORY / f"{name}.json").read_text(encoding="utf-8")
        )
        for name in CATALOG_FILES
    }
    counts = {name: len(items) for name, items in catalog.items()}
    if counts != CATALOG_COUNTS:
        raise RuntimeError(f"builtin asset catalog count mismatch: {counts}")
    digest = hashlib.sha256(_canonical(catalog)).hexdigest()
    if metadata.get("digest") != f"sha256:{digest}":
        raise RuntimeError("builtin asset catalog digest mismatch")
    if metadata.get("catalog_version") != CATALOG_VERSION:
        raise RuntimeError("builtin asset catalog version mismatch")
    if metadata.get("schema_version") != 1:
        raise RuntimeError("builtin asset catalog schema version mismatch")
    metadata_counts = {
        "groups": metadata.get("group_count"),
        "models": metadata.get("model_count"),
        "fields": metadata.get("field_count"),
        "unique_rules": metadata.get("unique_rule_count"),
    }
    if metadata_counts != {
        key: CATALOG_COUNTS[key]
        for key in ("groups", "models", "fields", "unique_rules")
    }:
        raise RuntimeError("builtin asset catalog metadata count mismatch")

    group_keys = {item["key"] for item in catalog["groups"]}
    model_keys = {item["key"] for item in catalog["models"]}
    if (
        len(group_keys) != CATALOG_COUNTS["groups"]
        or len(model_keys) != CATALOG_COUNTS["models"]
    ):
        raise RuntimeError("builtin asset catalog contains duplicate keys")
    if any(item["group_key"] not in group_keys for item in catalog["models"]):
        raise RuntimeError("builtin asset catalog contains unknown model groups")
    if any(item["model_key"] not in model_keys for item in catalog["fields"]):
        raise RuntimeError("builtin asset catalog contains unknown field models")
    field_identities = {
        (item["model_key"], item["field_key"]) for item in catalog["fields"]
    }
    if len(field_identities) != CATALOG_COUNTS["fields"]:
        raise RuntimeError("builtin asset catalog contains duplicate fields")
    field_labels = {
        (item["model_key"], _normalized_field_label(item.get("label")))
        for item in catalog["fields"]
    }
    if (
        any(not label for _, label in field_labels)
        or len(field_labels) != CATALOG_COUNTS["fields"]
    ):
        raise RuntimeError("builtin asset catalog contains duplicate field names")
    if any(item["data_type"] not in FIELD_DATA_TYPES for item in catalog["fields"]):
        raise RuntimeError("builtin asset catalog contains unsupported field types")
    if any(
        item.get("ref_ci_type_id") not in model_keys
        for item in catalog["fields"]
        if item["data_type"] == "ref"
    ):
        raise RuntimeError("builtin asset catalog contains invalid model references")
    if (
        sum(bool(item.get("is_derived")) for item in catalog["fields"])
        != DERIVED_FIELD_COUNT
    ):
        raise RuntimeError("builtin asset catalog derived-field count mismatch")
    if any(item["model_key"] not in model_keys for item in catalog["unique_rules"]):
        raise RuntimeError("builtin asset catalog contains unknown unique-rule models")
    for rule in catalog["unique_rules"]:
        if any(
            (rule["model_key"], field_key) not in field_identities
            for field_key in rule["field_keys"]
        ):
            raise RuntimeError(
                "builtin asset catalog unique rule contains unknown fields"
            )
    source_icons = {
        item.get("source_icon") for item in catalog["models"] if item.get("source_icon")
    }
    if {item.get("source_icon") for item in catalog["icon_aliases"]} != source_icons:
        raise RuntimeError("builtin asset catalog icon aliases are incomplete")
    return {"metadata": metadata, **catalog}


async def _lock_tenant(tenant: str, connection) -> None:
    row = (
        await Tenant.filter(id=tenant, deleted=False)
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if not row:
        raise CustomError(ErrorCode.NOT_FOUND)


async def _bulk_create(model, items: list, connection) -> None:
    if items:
        await model.bulk_create(items, batch_size=500, using_db=connection)


async def _upsert_builtin_groups(
    tenant: str,
    catalog: dict,
    operator: str | None,
    connection,
    *,
    preserve_existing: bool = False,
) -> tuple[dict, int]:
    existing = {
        item.key: item
        for item in await SupplyChainCiModelGroup.filter(tenant_id=tenant)
        .using_db(connection)
        .all()
    }
    created = []
    updated = 0
    for definition in catalog["groups"]:
        item = existing.get(definition["key"])
        values = {
            "name": definition["name"],
            "sort": definition["sort"],
            "is_builtin": True,
            "is_locked": False,
            "builtin_version": CATALOG_VERSION,
            "builtin_hash": definition["builtin_hash"],
            "deleted": False,
            "updater": operator,
        }
        if item is None:
            item = SupplyChainCiModelGroup(
                tenant_id=tenant,
                key=definition["key"],
                creator=operator,
                **values,
            )
            created.append(item)
            existing[item.key] = item
        elif not item.is_builtin:
            raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
        elif not preserve_existing and (
            item.builtin_hash != definition["builtin_hash"] or item.deleted
        ):
            for key, value in values.items():
                setattr(item, key, value)
            await item.save(using_db=connection, update_fields=[*values, "utime"])
            updated += 1
    await _bulk_create(SupplyChainCiModelGroup, created, connection)
    return existing, len(created) + updated


async def _upsert_builtin_models(
    tenant: str,
    catalog: dict,
    groups: dict,
    operator: str | None,
    connection,
    *,
    preserve_existing: bool = False,
) -> tuple[dict, int]:
    existing = {
        item.key: item
        for item in await SupplyChainCiType.filter(tenant_id=tenant)
        .using_db(connection)
        .all()
    }
    created = []
    updated = 0
    for definition in catalog["models"]:
        group = groups[definition["group_key"]]
        values = {
            "name": definition["name"],
            "icon": definition["icon"],
            "group_id": group.id,
            "group_key": group.key,
            "primary_field": definition["primary_field"],
            "is_locked": False,
            "is_builtin": True,
            "builtin_key": definition["key"],
            "builtin_version": CATALOG_VERSION,
            "builtin_hash": definition["builtin_hash"],
            "owner": "product",
            "sort": definition["sort"],
            "deleted": False,
            "updater": operator,
        }
        item = existing.get(definition["key"])
        if item is None:
            item = SupplyChainCiType(
                tenant_id=tenant,
                key=definition["key"],
                status=CI_TYPE_STATUS_DISABLED,
                creator=operator,
                **values,
            )
            created.append(item)
            existing[item.key] = item
        elif not item.is_builtin or item.owner != "product":
            raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
        elif not preserve_existing and (
            item.builtin_hash != definition["builtin_hash"] or item.deleted
        ):
            for key, value in values.items():
                setattr(item, key, value)
            await item.save(using_db=connection, update_fields=[*values, "utime"])
            updated += 1
    await _bulk_create(SupplyChainCiType, created, connection)
    return existing, len(created) + updated


async def _upsert_builtin_fields(
    tenant: str,
    catalog: dict,
    models: dict,
    operator: str | None,
    connection,
    *,
    preserve_existing: bool = False,
) -> int:
    existing = {
        item.builtin_key: item
        for item in await SupplyChainCiFieldDef.filter(
            tenant_id=tenant,
            is_builtin=True,
        )
        .using_db(connection)
        .all()
        if item.builtin_key
    }
    occupied = {
        (item.ci_type_id, item.field_key)
        for item in await SupplyChainCiFieldDef.filter(
            tenant_id=tenant,
            deleted=False,
        )
        .using_db(connection)
        .all()
        if not item.is_builtin
    }
    created = []
    updated = 0
    for definition in catalog["fields"]:
        model = models[definition["model_key"]]
        ref_model = models.get(definition.get("ref_ci_type_id"))
        values = {
            "ci_type_id": model.id,
            "ci_type_key": model.key,
            "field_key": definition["field_key"],
            "source_field_key": definition["source_field_key"],
            "label": definition["label"],
            "data_type": definition["data_type"],
            "enum_options": definition["enum_options"],
            "options": definition["options"],
            "ref_ci_type_id": ref_model.id if ref_model else None,
            "ref_ci_type": ref_model.key if ref_model else None,
            "required": definition["required"],
            "unique": definition["unique"],
            "editable": definition["editable"],
            "is_derived": definition["is_derived"],
            "user_prompt": definition["user_prompt"],
            "default_value": definition["default_value"],
            "field_group": definition["field_group"],
            "field_group_sort": definition["field_group_sort"],
            "sort": definition["sort"],
            "is_locked": False,
            "is_builtin": True,
            "builtin_key": definition["builtin_key"],
            "builtin_version": CATALOG_VERSION,
            "builtin_hash": definition["builtin_hash"],
            "owner": "product",
            "deleted": False,
            "updater": operator,
        }
        item = existing.get(definition["builtin_key"])
        if item is None:
            if (model.id, definition["field_key"]) in occupied:
                raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
            created.append(
                SupplyChainCiFieldDef(
                    tenant_id=tenant,
                    creator=operator,
                    **values,
                )
            )
        elif not preserve_existing and (
            item.builtin_hash != definition["builtin_hash"] or item.deleted
        ):
            for key, value in values.items():
                setattr(item, key, value)
            await item.save(using_db=connection, update_fields=[*values, "utime"])
            updated += 1
    await _bulk_create(SupplyChainCiFieldDef, created, connection)
    return len(created) + updated


async def _upsert_builtin_unique_rules(
    tenant: str,
    catalog: dict,
    models: dict,
    operator: str | None,
    connection,
    *,
    preserve_existing: bool = False,
) -> int:
    existing = {
        item.builtin_key: item
        for item in await SupplyChainCiUniqueRule.filter(
            tenant_id=tenant,
            is_builtin=True,
        )
        .using_db(connection)
        .all()
        if item.builtin_key
    }
    occupied = {
        (item.ci_type_id, item.key)
        for item in await SupplyChainCiUniqueRule.filter(
            tenant_id=tenant,
            deleted=False,
        )
        .using_db(connection)
        .all()
        if not item.is_builtin
    }
    created = []
    updated = 0
    for definition in catalog["unique_rules"]:
        model = models[definition["model_key"]]
        values = {
            "ci_type_id": model.id,
            "ci_type_key": model.key,
            "key": definition["builtin_key"][:64],
            "field_keys": definition["field_keys"],
            "sort": definition["sort"],
            "is_locked": False,
            "is_builtin": True,
            "builtin_key": definition["builtin_key"],
            "builtin_version": CATALOG_VERSION,
            "builtin_hash": definition["builtin_hash"],
            "owner": "product",
            "deleted": False,
            "updater": operator,
        }
        item = existing.get(definition["builtin_key"])
        if item is None:
            if (model.id, definition["builtin_key"][:64]) in occupied:
                raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
            created.append(
                SupplyChainCiUniqueRule(tenant_id=tenant, creator=operator, **values)
            )
        elif not preserve_existing and (
            item.builtin_hash != definition["builtin_hash"] or item.deleted
        ):
            for key, value in values.items():
                setattr(item, key, value)
            await item.save(using_db=connection, update_fields=[*values, "utime"])
            updated += 1
    await _bulk_create(SupplyChainCiUniqueRule, created, connection)
    return len(created) + updated


async def _ensure_activations(
    tenant: str,
    catalog: dict,
    operator: str | None,
    connection,
) -> int:
    existing = {
        item.subject_key
        for item in await SupplyChainBuiltinActivation.filter(
            tenant_id=tenant,
            subject_type="model",
        )
        .using_db(connection)
        .all()
    }
    created = [
        SupplyChainBuiltinActivation(
            tenant_id=tenant,
            subject_type="model",
            subject_key=definition["key"],
            enabled=False,
            revision=0,
            updater=operator,
        )
        for definition in catalog["models"]
        if definition["key"] not in existing
    ]
    await _bulk_create(SupplyChainBuiltinActivation, created, connection)
    return len(created)


async def _record_catalog_event(
    tenant: str,
    request_id: str,
    status: str,
    metadata: dict,
    operator: str | None,
    connection,
    *,
    before_state: dict | None = None,
    after_state: dict | None = None,
    changes: dict | None = None,
    failure_reason: str | None = None,
) -> None:
    await SupplyChainBuiltinCatalogEvent.create(
        using_db=connection,
        tenant_id=tenant,
        request_id=request_id,
        action="reconcile",
        status=status,
        catalog_version=CATALOG_VERSION,
        catalog_digest=metadata["digest"],
        before_state=before_state or {},
        after_state=after_state or {},
        changes=changes or {},
        failure_reason=failure_reason,
        operator=operator,
    )


async def _reconcile_builtin_catalog_once(
    tenant: str,
    operator: str | None = None,
    request_id: str | None = None,
) -> dict:
    catalog = load_builtin_catalog()
    metadata = catalog["metadata"]
    request_id = request_id or uuid.uuid4().hex
    async with in_transaction() as connection:
        await _lock_tenant(tenant, connection)
        state = (
            await SupplyChainBuiltinCatalogState.filter(
                tenant_id=tenant,
                catalog_key=metadata["catalog_key"],
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        before_state = state.json if state else {}
        metadata_changed = bool(
            state
            and (
                state.catalog_version != CATALOG_VERSION
                or state.catalog_digest != metadata["digest"]
            )
        )
        preserve_existing = bool(state and state.status == "ready")
        if preserve_existing and not metadata_changed:
            await _record_catalog_event(
                tenant,
                request_id,
                "unchanged",
                metadata,
                operator,
                connection,
                before_state=before_state,
                after_state=state.json,
            )
            return {
                "request_id": request_id,
                "state": state.json,
                "changed": False,
                "changes": {},
                **CATALOG_COUNTS,
            }

        groups, group_changes = await _upsert_builtin_groups(
            tenant,
            catalog,
            operator,
            connection,
            preserve_existing=preserve_existing,
        )
        models, model_changes = await _upsert_builtin_models(
            tenant,
            catalog,
            groups,
            operator,
            connection,
            preserve_existing=preserve_existing,
        )
        field_changes = await _upsert_builtin_fields(
            tenant,
            catalog,
            models,
            operator,
            connection,
            preserve_existing=preserve_existing,
        )
        rule_changes = await _upsert_builtin_unique_rules(
            tenant,
            catalog,
            models,
            operator,
            connection,
            preserve_existing=preserve_existing,
        )
        activation_changes = await _ensure_activations(
            tenant, catalog, operator, connection
        )
        if state is None:
            state = await SupplyChainBuiltinCatalogState.create(
                using_db=connection,
                tenant_id=tenant,
                catalog_key=metadata["catalog_key"],
                catalog_version=CATALOG_VERSION,
                catalog_digest=metadata["digest"],
                revision=0,
                status="ready",
                updater=operator,
            )
        else:
            state.catalog_version = CATALOG_VERSION
            state.catalog_digest = metadata["digest"]
            state.status = "ready"
            state.updater = operator
            await state.save(
                using_db=connection,
                update_fields=[
                    "catalog_version",
                    "catalog_digest",
                    "status",
                    "updater",
                    "utime",
                ],
            )
        changes = {
            "groups": group_changes,
            "models": model_changes,
            "fields": field_changes,
            "unique_rules": rule_changes,
            "activations": activation_changes,
        }
        if metadata_changed:
            changes["metadata"] = 1
        await _record_catalog_event(
            tenant,
            request_id,
            "succeeded",
            metadata,
            operator,
            connection,
            before_state=before_state,
            after_state=state.json,
            changes=changes,
        )
        return {
            "request_id": request_id,
            "state": state.json,
            "changed": True,
            "changes": changes,
            **CATALOG_COUNTS,
        }


async def reconcile_builtin_catalog(
    tenant: str,
    operator: str | None = None,
    request_id: str | None = None,
) -> dict:
    if request_id is not None and (
        not isinstance(request_id, str) or not request_id or len(request_id) > 64
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)
    request_id = request_id or uuid.uuid4().hex
    try:
        return await _reconcile_builtin_catalog_once(
            tenant,
            operator,
            request_id,
        )
    except Exception as exc:
        metadata = load_builtin_catalog()["metadata"]
        async with in_transaction() as connection:
            await _lock_tenant(tenant, connection)
            state = (
                await SupplyChainBuiltinCatalogState.filter(
                    tenant_id=tenant,
                    catalog_key=metadata["catalog_key"],
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            before_state = state.json if state else {}
            if state is None:
                state = await SupplyChainBuiltinCatalogState.create(
                    using_db=connection,
                    tenant_id=tenant,
                    catalog_key=metadata["catalog_key"],
                    catalog_version=CATALOG_VERSION,
                    catalog_digest=metadata["digest"],
                    revision=0,
                    status="provision_failed",
                    updater=operator,
                )
            else:
                state.catalog_version = CATALOG_VERSION
                state.catalog_digest = metadata["digest"]
                state.status = "provision_failed"
                state.updater = operator
                await state.save(
                    using_db=connection,
                    update_fields=[
                        "catalog_version",
                        "catalog_digest",
                        "status",
                        "updater",
                        "utime",
                    ],
                )
            await _record_catalog_event(
                tenant,
                request_id,
                "failed",
                metadata,
                operator,
                connection,
                before_state=before_state,
                after_state=state.json,
                failure_reason=type(exc).__name__,
            )
        raise


async def reconcile_all_tenant_builtin_catalogs(
    operator: str = "system-startup-catalog-reconcile",
) -> dict:
    tenants = await Tenant.filter(deleted=False).order_by("id").all()
    result = {
        "total": len(tenants),
        "changed": 0,
        "unchanged": 0,
        "failed": [],
    }
    for tenant in tenants:
        try:
            reconciled = await reconcile_builtin_catalog(
                tenant.id,
                operator,
                request_id=uuid.uuid4().hex,
            )
            outcome = "changed" if reconciled["changed"] else "unchanged"
            result[outcome] += 1
        except Exception as exc:
            result["failed"].append(
                {
                    "tenant_id": tenant.id,
                    "failure_reason": type(exc).__name__,
                }
            )
    return result


async def ensure_builtin_catalog(
    tenant: str, operator: str | None = None
) -> SupplyChainBuiltinCatalogState:
    metadata = load_builtin_catalog()["metadata"]
    state = await SupplyChainBuiltinCatalogState.filter(
        tenant_id=tenant,
        catalog_key=metadata["catalog_key"],
        catalog_version=CATALOG_VERSION,
        catalog_digest=metadata["digest"],
        status="ready",
        deleted=False,
    ).first()
    if state:
        return state
    await reconcile_builtin_catalog(tenant, operator)
    state = await SupplyChainBuiltinCatalogState.filter(
        tenant_id=tenant,
        catalog_key=metadata["catalog_key"],
        deleted=False,
    ).first()
    if not state:
        raise CustomError(ErrorCode.OPERATION_FAILED)
    return state


async def builtin_catalog_status(tenant: str, operator: str | None = None) -> dict:
    state = await ensure_builtin_catalog(tenant, operator)
    catalog = load_builtin_catalog()
    return {
        "state": state.json,
        "counts": dict(CATALOG_COUNTS),
        "source_snapshot_at": catalog["metadata"]["source_snapshot_at"],
        "schema_version": catalog["metadata"]["schema_version"],
    }


async def save_model_group(
    tenant: str,
    data: dict,
    operator: str | None = None,
) -> SupplyChainCiModelGroup:
    if not isinstance(data, dict):
        raise CustomError(ErrorCode.PARAM_ERROR)
    name = str(data.get("name") or "").strip()
    key = str(data.get("key") or "").strip()
    if not name or (key and not GROUP_KEY_RE.match(key)):
        raise CustomError(ErrorCode.PARAM_ERROR)
    async with in_transaction() as connection:
        await _lock_tenant(tenant, connection)
        group = None
        if data.get("id"):
            group = (
                await SupplyChainCiModelGroup.filter(
                    tenant_id=tenant,
                    id=data["id"],
                    deleted=False,
                )
                .using_db(connection)
                .select_for_update()
                .first()
            )
            if not group:
                raise CustomError(ErrorCode.NOT_FOUND)
            if key and key != group.key:
                raise CustomError(ErrorCode.PARAM_ERROR)
        else:
            key = key or f"group_{uuid.uuid4().hex}"
            if (
                await SupplyChainCiModelGroup.filter(
                    tenant_id=tenant,
                    key=key,
                )
                .using_db(connection)
                .exists()
            ):
                raise CustomError(ErrorCode.DATA_ALREADY_EXISTS)
        try:
            sort = int(data.get("sort", getattr(group, "sort", 10000)) or 0)
        except (TypeError, ValueError):
            raise CustomError(ErrorCode.PARAM_ERROR) from None
        if sort < 0:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if group:
            group.name = name
            group.sort = sort
            group.updater = operator
            await group.save(
                using_db=connection,
                update_fields=["name", "sort", "updater", "utime"],
            )
            return group
        return await SupplyChainCiModelGroup.create(
            using_db=connection,
            tenant_id=tenant,
            key=key,
            name=name,
            sort=sort,
            is_builtin=False,
            is_locked=False,
            creator=operator,
            updater=operator,
        )


async def delete_model_group(tenant: str, group_id: str) -> dict:
    async with in_transaction() as connection:
        await _lock_tenant(tenant, connection)
        group = (
            await SupplyChainCiModelGroup.filter(
                tenant_id=tenant,
                id=group_id,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not group:
            raise CustomError(ErrorCode.NOT_FOUND)
        if (
            await SupplyChainCiType.filter(
                tenant_id=tenant,
                group_id=group.id,
                deleted=False,
            )
            .using_db(connection)
            .exists()
        ):
            raise CustomError(ErrorCode.ASSET_MODEL_HAS_DEPENDENCIES)
        group.deleted = True
        await group.save(using_db=connection, update_fields=["deleted", "utime"])
        return {"id": group.id}


async def reorder_model_groups(
    tenant: str,
    group_ids: list[str],
    operator: str | None = None,
) -> list[SupplyChainCiModelGroup]:
    if (
        not isinstance(group_ids, list)
        or any(not isinstance(group_id, str) or not group_id for group_id in group_ids)
        or len(group_ids) != len(set(group_ids))
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)
    async with in_transaction() as connection:
        await _lock_tenant(tenant, connection)
        groups = (
            await SupplyChainCiModelGroup.filter(tenant_id=tenant, deleted=False)
            .using_db(connection)
            .order_by("sort", "key")
            .select_for_update()
            .all()
        )
        by_id = {item.id: item for item in groups}
        if set(group_ids) != set(by_id):
            raise CustomError(ErrorCode.PARAM_ERROR)
        ordered = [by_id[group_id] for group_id in group_ids]
        for sort, group in enumerate(ordered, start=1):
            group.sort = sort
            group.updater = operator
            await group.save(
                using_db=connection,
                update_fields=["sort", "updater", "utime"],
            )
        return ordered


def _group_status(enabled_count: int, total_count: int) -> str:
    if total_count and enabled_count == total_count:
        return "enabled"
    if enabled_count:
        return "partial"
    return "disabled"


async def list_model_groups(
    tenant: str,
    *,
    keyword: str | None = None,
    status: str | None = None,
    include_models: bool = True,
    include_instance_count: bool = False,
) -> dict:
    state = await ensure_builtin_catalog(tenant)
    groups = (
        await SupplyChainCiModelGroup.filter(
            tenant_id=tenant,
            deleted=False,
        )
        .order_by("sort", "key")
        .all()
    )
    models = (
        await SupplyChainCiType.filter(
            tenant_id=tenant,
            deleted=False,
        )
        .order_by("group_key", "sort", "key")
        .all()
    )
    instance_counts = {}
    if include_instance_count and models:
        for ci in await SupplyChainCi.filter(
            tenant_id=tenant,
            ci_type_id__in=[item.id for item in models],
            deleted=False,
        ).all():
            instance_counts[ci.ci_type_id] = instance_counts.get(ci.ci_type_id, 0) + 1
    keyword_folded = (keyword or "").strip().casefold()
    result = []
    for group in groups:
        all_group_models = [item for item in models if item.group_id == group.id]
        group_models = list(all_group_models)
        if keyword_folded:
            group_matches = (
                keyword_folded in group.name.casefold()
                or keyword_folded in group.key.casefold()
            )
            group_models = [
                item
                for item in group_models
                if group_matches
                or keyword_folded in item.name.casefold()
                or keyword_folded in item.key.casefold()
            ]
        enabled_count = sum(
            item.status == CI_TYPE_STATUS_ACTIVE for item in all_group_models
        )
        derived_status = _group_status(enabled_count, len(all_group_models))
        if status == "enabled":
            group_models = [
                item for item in group_models if item.status == CI_TYPE_STATUS_ACTIVE
            ]
        elif status == "disabled":
            group_models = [
                item for item in group_models if item.status == CI_TYPE_STATUS_DISABLED
            ]
        elif status not in {None, "", "all", "partial"}:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if status == "partial" and derived_status != "partial":
            continue
        if keyword_folded and not group_models:
            continue
        if status in {"enabled", "disabled"} and not group_models:
            continue
        payload = group.json
        payload.update(
            {
                "total_count": len(all_group_models),
                "enabled_count": enabled_count,
                "disabled_count": len(all_group_models) - enabled_count,
                "status": derived_status,
            }
        )
        if include_models:
            payload["models"] = [
                {
                    **item.json,
                    "instance_count": instance_counts.get(item.id, 0),
                }
                for item in group_models
            ]
        result.append(payload)
    return {
        "catalog": state.json,
        "items": result,
        "total": len(result),
    }


async def _resolve_activation_models(
    tenant: str,
    scope: str,
    subject_keys: list[str],
    *,
    connection=None,
) -> list[SupplyChainCiType]:
    query = SupplyChainCiType.filter(tenant_id=tenant, deleted=False)
    if connection is not None:
        query = query.using_db(connection)
    all_models = await query.order_by("key").all()
    if scope == "all":
        return all_models
    if not subject_keys or any(
        not isinstance(key, str) or not key for key in subject_keys
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)
    wanted = set(subject_keys)
    if scope == "model":
        selected = [
            item for item in all_models if item.key in wanted or item.id in wanted
        ]
    elif scope == "group":
        selected = [
            item
            for item in all_models
            if item.group_key in wanted or item.group_id in wanted
        ]
    else:
        selected = [
            item
            for item in all_models
            if item.key in wanted
            or item.id in wanted
            or item.group_key in wanted
            or item.group_id in wanted
        ]
    if not selected:
        raise CustomError(ErrorCode.NOT_FOUND)
    return selected


async def _activation_impact(
    tenant: str,
    models: list[SupplyChainCiType],
    enabled: bool,
    *,
    connection=None,
) -> dict:
    model_keys = {item.key for item in models}
    query = SupplyChainCiFieldDef.filter(
        tenant_id=tenant,
        data_type="ref",
        deleted=False,
    )
    if connection is not None:
        query = query.using_db(connection)
    reference_fields = await query.all()
    all_models_query = SupplyChainCiType.filter(tenant_id=tenant, deleted=False)
    if connection is not None:
        all_models_query = all_models_query.using_db(connection)
    all_models = await all_models_query.all()
    model_key_by_ref = {
        ref: item.key for item in all_models for ref in (item.id, item.key)
    }
    target_enabled = {
        item.key: enabled
        if item.key in model_keys
        else item.status == CI_TYPE_STATUS_ACTIVE
        for item in all_models
    }
    active_reference_fields = 0
    dormant_reference_fields = 0
    cross_group_dependencies = []
    selected_group_keys = {item.group_key for item in models}
    for field in reference_fields:
        target_key = model_key_by_ref.get(field.ref_ci_type_id or field.ref_ci_type)
        endpoints = {field.ci_type_key, target_key} - {None}
        if not endpoints & model_keys:
            continue
        if all(target_enabled.get(key, False) for key in endpoints):
            active_reference_fields += 1
        else:
            dormant_reference_fields += 1
        for endpoint in sorted(endpoints - model_keys):
            endpoint_model = next(
                (item for item in all_models if item.key == endpoint), None
            )
            if endpoint_model and endpoint_model.group_key not in selected_group_keys:
                cross_group_dependencies.append(
                    {
                        "field_key": field.field_key,
                        "source_model_key": field.ci_type_key,
                        "model_key": endpoint,
                        "group_key": endpoint_model.group_key,
                        "enabled": endpoint_model.status == CI_TYPE_STATUS_ACTIVE,
                    }
                )
    blocking_reasons = []
    if enabled:
        required_directory_fields = await SupplyChainCiFieldDef.filter(
            tenant_id=tenant,
            ci_type_id__in=[item.id for item in models],
            data_type__in=["organization", "user"],
            required=True,
            deleted=False,
        ).all()
        required_types = {field.data_type for field in required_directory_fields}
        if "organization" in required_types:
            tenant_query = Tenant.filter(id=tenant, deleted=False)
            if connection is not None:
                tenant_query = tenant_query.using_db(connection)
            if not await tenant_query.exists():
                blocking_reasons.append("organization_directory_unavailable")
        if "user" in required_types:
            account_query = Account.filter(
                deleted=False,
                disabled=False,
                member__id__in=[tenant],
            )
            if connection is not None:
                account_query = account_query.using_db(connection)
            if not await account_query.exists():
                blocking_reasons.append("user_directory_empty")
    instance_query = SupplyChainCi.filter(
        tenant_id=tenant,
        ci_type_id__in=[item.id for item in models],
        deleted=False,
    )
    if connection is not None:
        instance_query = instance_query.using_db(connection)
    return {
        "affected_model_count": len(models),
        "active_reference_field_count": active_reference_fields,
        "dormant_reference_field_count": dormant_reference_fields,
        "existing_instance_count": await instance_query.count(),
        "blocking_reasons": blocking_reasons,
        "cross_group_dependencies": cross_group_dependencies,
        "model_keys": sorted(model_keys),
    }


async def preview_builtin_activation(
    tenant: str,
    data: dict,
    operator: str | None,
) -> dict:
    action = data.get("action")
    scope = data.get("scope")
    subject_keys = list(dict.fromkeys(data.get("subject_keys") or []))
    if action not in ACTIVATION_ACTIONS or scope not in ACTIVATION_SCOPES:
        raise CustomError(ErrorCode.PARAM_ERROR)
    state = await ensure_builtin_catalog(tenant, operator)
    if data.get("expected_catalog_version") != state.catalog_version:
        raise CustomError(ErrorCode.PARAM_ERROR)
    models = await _resolve_activation_models(tenant, scope, subject_keys)
    impact = await _activation_impact(tenant, models, action == "enable")
    token = uuid.uuid4().hex
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=10
    )
    await SupplyChainBuiltinActivationPreview.create(
        tenant_id=tenant,
        token=token,
        action=action,
        scope=scope,
        subject_keys=subject_keys,
        catalog_version=state.catalog_version,
        tenant_revision=state.revision,
        impact=impact,
        expires_at=expires_at,
        used=False,
        creator=operator,
    )
    return {
        "preview_token": token,
        "tenant_revision": state.revision,
        "catalog_version": state.catalog_version,
        "expires_at": expires_at.isoformat(),
        **impact,
    }


async def apply_builtin_activation(
    tenant: str,
    data: dict,
    operator: str | None,
) -> dict:
    token = data.get("preview_token")
    request_id = data.get("request_id")
    expected_revision = data.get("expected_tenant_revision")
    if (
        not isinstance(token, str)
        or not token
        or not isinstance(request_id, str)
        or not request_id
    ):
        raise CustomError(ErrorCode.PARAM_ERROR)
    async with in_transaction() as connection:
        await _lock_tenant(tenant, connection)
        previous_event = (
            await SupplyChainBuiltinActivationEvent.filter(
                tenant_id=tenant,
                request_id=request_id,
                deleted=False,
            )
            .using_db(connection)
            .first()
        )
        if previous_event:
            return previous_event.json
        preview = (
            await SupplyChainBuiltinActivationPreview.filter(
                tenant_id=tenant,
                token=token,
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        state = (
            await SupplyChainBuiltinCatalogState.filter(
                tenant_id=tenant,
                catalog_version=CATALOG_VERSION,
                status="ready",
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        if (
            not preview
            or not state
            or preview.used
            or preview.expires_at <= now
            or preview.catalog_version != state.catalog_version
            or preview.tenant_revision != state.revision
            or expected_revision != state.revision
        ):
            raise CustomError(ErrorCode.PARAM_ERROR)
        models = await _resolve_activation_models(
            tenant,
            preview.scope,
            preview.subject_keys or [],
            connection=connection,
        )
        enabled = preview.action == "enable"
        target_status = CI_TYPE_STATUS_ACTIVE if enabled else CI_TYPE_STATUS_DISABLED
        before_state = {item.key: item.status for item in models}
        new_revision = state.revision + 1
        for model in models:
            model.status = target_status
            model.updater = operator
            await model.save(
                using_db=connection,
                update_fields=["status", "updater", "utime"],
            )
            if model.is_builtin:
                activation = (
                    await SupplyChainBuiltinActivation.filter(
                        tenant_id=tenant,
                        subject_type="model",
                        subject_key=model.key,
                    )
                    .using_db(connection)
                    .select_for_update()
                    .first()
                )
                if not activation:
                    activation = SupplyChainBuiltinActivation(
                        tenant_id=tenant,
                        subject_type="model",
                        subject_key=model.key,
                    )
                activation.enabled = enabled
                activation.revision = new_revision
                activation.updater = operator
                await activation.save(using_db=connection)
        state.revision = new_revision
        state.onboarding_completed = True
        state.updater = operator
        await state.save(
            using_db=connection,
            update_fields=[
                "revision",
                "onboarding_completed",
                "updater",
                "utime",
            ],
        )
        preview.used = True
        await preview.save(using_db=connection, update_fields=["used", "utime"])
        after_state = {item.key: target_status for item in models}
        event = await SupplyChainBuiltinActivationEvent.create(
            using_db=connection,
            tenant_id=tenant,
            request_id=request_id,
            action=preview.action,
            scope=preview.scope,
            subject_keys=preview.subject_keys or [],
            before_state=before_state,
            after_state=after_state,
            impact=preview.impact or {},
            catalog_version=state.catalog_version,
            tenant_revision=new_revision,
            operator=operator,
        )
        return event.json


async def complete_builtin_onboarding(
    tenant: str,
    operator: str | None,
) -> dict:
    await ensure_builtin_catalog(tenant, operator)
    async with in_transaction() as connection:
        await _lock_tenant(tenant, connection)
        state = (
            await SupplyChainBuiltinCatalogState.filter(
                tenant_id=tenant,
                catalog_version=CATALOG_VERSION,
                status="ready",
                deleted=False,
            )
            .using_db(connection)
            .select_for_update()
            .first()
        )
        if not state:
            raise CustomError(ErrorCode.NOT_FOUND)
        if not state.onboarding_completed:
            state.onboarding_completed = True
            state.updater = operator
            await state.save(
                using_db=connection,
                update_fields=["onboarding_completed", "updater", "utime"],
            )
        return state.json
