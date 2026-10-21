"""Tenant-scoped network policy collection plan (one per tenant)."""
from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from models import AssetInstance, ToolAction, ToolResources
from models.network_access_control import (
    NetworkAccessPolicyCollectionItem,
    NetworkAccessPolicyCollectionPlan,
    NetworkAccessPolicyCollectionRun,
)
from services.action_collection import timer as collection_timer
from services.action_collection.errors import CollectionError
from services.inspection.logic import is_valid_schedule_cron
from services.network_access_control.collection.constants import (
    LOCK_NAMESPACE, MAX_RESOURCES, POLICY_DEVICE_MODEL_KEYS, TIMER_PREFIX,
)
from services.network_topology.canonicalization import canonicalize_json

PLAN_FIELDS = ("id", "enabled", "schedule_cron", "concurrency", "per_item_timeout_seconds",
               "run_timeout_seconds", "notify_enabled", "notify_config", "last_run_id",
               "consecutive_failures", "revision")
ITEM_FIELDS = ("id", "app_name", "app_version", "action_func", "action_input", "order")
DEFAULTS = dict(id=None, enabled=False, schedule_cron="0 0 3 * * *", concurrency=8,
                per_item_timeout_seconds=300, run_timeout_seconds=3600, notify_enabled=False,
                notify_config=None, last_run_id=None, consecutive_failures=0, revision=0)


def _invalid(reason):
    return CollectionError("PLAN_INVALID", 400, reason)


def _reject_tenant(value):
    if isinstance(value, dict):
        if {"tenant", "tenant_id"} & value.keys():
            raise _invalid("请求不得携带租户字段")
        for child in value.values():
            _reject_tenant(child)
    elif isinstance(value, list):
        for child in value:
            _reject_tenant(child)


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise _invalid(f"{name} 必须是 {low}-{high} 的整数")
    return value


def _boolean(raw, key):
    value = raw.get(key, False)
    if type(value) is not bool:
        raise _invalid(f"{key} 必须是布尔值")
    return value


def _identifier(value, message):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 36:
        raise _invalid(message)
    return value.strip()


def validate_plan_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise _invalid("请求必须是对象")
    _reject_tenant(payload)
    if not is_valid_schedule_cron(payload.get("schedule_cron")):
        raise _invalid("采集周期格式非法")
    data = {key: _boolean(payload, key) for key in ("enabled", "notify_enabled")}
    data["schedule_cron"] = payload["schedule_cron"]
    for key, low, high in (("concurrency", 1, 64), ("per_item_timeout_seconds", 30, 3600),
                           ("run_timeout_seconds", 600, 3600)):
        data[key] = _integer(payload.get(key), key, low, high)
    data["expected_revision"] = _integer(payload.get("expected_revision", 0), "expected_revision", 0, 2**31 - 1)
    config = payload.get("notify_config")
    if config is not None and not isinstance(config, dict):
        raise _invalid("通知配置必须是对象")
    data["notify_config"] = deepcopy(config)
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        raise _invalid("采集项必须是数组")
    items, seen, owners = [], set(), {}
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise _invalid("采集项必须是对象")
        app, func = raw.get("app_name"), raw.get("action_func")
        if (not isinstance(app, str) or not app.strip() or len(app.strip()) > 255
                or not isinstance(func, str) or not func.strip() or len(func.strip()) > 255):
            raise _invalid("采集项必须指定应用和动作")
        app, func = app.strip(), func.strip()
        action_input = raw.get("action_input", {})
        if not isinstance(action_input, dict):
            raise _invalid("动作参数必须是对象")
        try:
            key = (app, func, canonicalize_json(action_input))
        except (ValueError, TypeError) as exc:
            raise _invalid("动作参数不是有效 JSON") from exc
        if key in seen:
            raise _invalid("存在重复的采集项")
        seen.add(key)
        resources = raw.get("resources", [])
        if not isinstance(resources, list):
            raise _invalid("资源必须是数组")
        normalized, local = [], set()
        for entry in resources:
            if not isinstance(entry, dict):
                raise _invalid("资源必须是对象")
            rid = _identifier(entry.get("resource_id"), "资源 ID 非法")
            asset = entry.get("device_asset_id")
            asset = None if asset in (None, "") else _identifier(asset, "设备资产 ID 非法")
            if rid in local:
                continue
            if owners.get(rid, index) != index:
                raise _invalid("同一资源只能出现在一个采集项中")
            owners[rid] = index
            local.add(rid)
            normalized.append({"resource_id": rid, "device_asset_id": asset})
        if not normalized:
            raise _invalid("采集项至少选择一台资源")
        version = raw.get("app_version")
        if version is not None and (not isinstance(version, str) or len(version) > 50):
            raise _invalid("应用版本非法")
        items.append(dict(app_name=app, app_version=version or None, action_func=func,
                          action_input=deepcopy(action_input), resources=normalized, enabled=True,
                          order=_integer(raw.get("order", index), "order", 0, 2**31 - 1)))
    if len(owners) > MAX_RESOURCES:
        raise _invalid(f"采集设备总数不能超过 {MAX_RESOURCES}")
    if data["enabled"] and not items:
        raise _invalid("定时采集至少需要一个采集项")
    data["items"] = items
    return data


class PlanRepository(Protocol):
    async def load(self, tenant_id): ...
    async def check_references(self, tenant_id, items) -> None: ...
    async def upsert(self, tenant_id, data, *, account_id): ...


class OrmPlanRepository:
    async def load(self, tenant_id):
        plan = await NetworkAccessPolicyCollectionPlan.filter(tenant_id=tenant_id, deleted=False).first()
        if plan is None:
            return None, []
        items = await NetworkAccessPolicyCollectionItem.filter(
            tenant_id=tenant_id, plan_id=plan.id, deleted=False).order_by("order", "id")
        return plan, items

    async def check_references(self, tenant_id, items):
        for item in items:
            filters = dict(tenant_id=tenant_id, deleted=False, tool_name=item["app_name"])
            if item["app_version"]:
                filters["version_number"] = item["app_version"]
            actions = await ToolAction.filter(**filters)
            if not any(isinstance(a.action, dict) and a.action.get("func") == item["action_func"] for a in actions):
                raise _invalid("采集动作不存在")
            for resource in item["resources"]:
                if not await ToolResources.filter(id=resource["resource_id"], tenant_id=tenant_id, deleted=False,
                                                  tool_name=item["app_name"], internal=False).exists():
                    raise _invalid("应用资源不存在或不可用")
                asset_id = resource["device_asset_id"]
                if asset_id is None:
                    continue
                asset = await AssetInstance.filter(id=asset_id, tenant_id=tenant_id, deleted=False).first()
                is_network = asset is not None and asset.asset_model_key in POLICY_DEVICE_MODEL_KEYS
                if not is_network:
                    raise _invalid("绑定的设备资产不存在或不是网络设备")

    async def upsert(self, tenant_id, data, *, account_id):
        values = {k: v for k, v in data.items() if k not in ("items", "expected_revision")}
        try:
            async with in_transaction() as db:
                plan = await NetworkAccessPolicyCollectionPlan.filter(
                    tenant_id=tenant_id, deleted=False).using_db(db).select_for_update().first()
                if data["expected_revision"] != (plan.revision if plan else 0):
                    raise CollectionError("PLAN_REVISION_CONFLICT", 409)
                if plan is None:
                    plan = await NetworkAccessPolicyCollectionPlan.create(
                        using_db=db, tenant_id=tenant_id, revision=1, updater=account_id, **values)
                else:
                    for key, value in values.items():
                        setattr(plan, key, value)
                    plan.revision += 1
                    plan.updater = account_id
                    await plan.save(using_db=db)
                await NetworkAccessPolicyCollectionItem.filter(
                    tenant_id=tenant_id, plan_id=plan.id, deleted=False).using_db(db).update(deleted=True)
                for item in data["items"]:
                    await NetworkAccessPolicyCollectionItem.create(using_db=db, tenant_id=tenant_id,
                                                                   plan_id=plan.id, **item)
        except IntegrityError:
            # Two first saves raced on uidx_policy_collection_plan_tenant.
            raise CollectionError("PLAN_REVISION_CONFLICT", 409) from None
        return plan


def default_plan_dict() -> dict:
    return deepcopy(DEFAULTS)


def _plan_dict(plan) -> dict:
    return {key: deepcopy(getattr(plan, key, DEFAULTS.get(key))) for key in PLAN_FIELDS}


def _item_dict(item) -> dict:
    return {**{key: deepcopy(getattr(item, key, None)) for key in ITEM_FIELDS},
            "resources": [{"resource_id": str(r["resource_id"]), "device_asset_id": r.get("device_asset_id")}
                          for r in (item.resources or [])]}


async def _last_run(tenant_id, plan):
    if plan is None or not getattr(plan, "last_run_id", None):
        return None
    from services.network_access_control.collection.runs import run_summary
    row = await NetworkAccessPolicyCollectionRun.filter(
        id=plan.last_run_id, tenant_id=tenant_id, deleted=False).first()
    return run_summary(row) if row else None


async def get_plan(tenant_id: str, *, repository=None) -> dict:
    repo = repository or OrmPlanRepository()
    plan, items = await repo.load(tenant_id)
    return {
        "plan": _plan_dict(plan) if plan is not None else default_plan_dict(),
        "items": [_item_dict(item) for item in items],
        "last_run": await _last_run(tenant_id, plan),
    }


async def sync_timer(plan) -> None:
    await collection_timer.reconcile_timer(
        plan, plan_model=NetworkAccessPolicyCollectionPlan, timer_prefix=TIMER_PREFIX,
        lock_namespace=LOCK_NAMESPACE)


async def save_plan(tenant_id: str, payload: dict, *, account_id: str, repository=None) -> dict:
    data = validate_plan_payload(payload)
    repo = repository or OrmPlanRepository()
    await repo.check_references(tenant_id, data["items"])
    plan = await repo.upsert(tenant_id, data, account_id=account_id)
    await sync_timer(plan)
    return await get_plan(tenant_id, repository=repo)
