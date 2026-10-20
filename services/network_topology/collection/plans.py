"""Tenant-scoped collection plans and retryable internal timer reconciliation."""
from __future__ import annotations

from copy import deepcopy
from contextlib import asynccontextmanager
import asyncpg  # noqa: F401 - tests patch the shared driver through this module
from tortoise import connections  # noqa: F401 - tests patch the shared connection registry
from tortoise.expressions import RawSQL

from tortoise.transactions import in_transaction
from core.maintenance import DEFAULT_TENANT_ID
from libs.kafka import aproduce
from models import Timed, ToolAction, ToolResources
from models.network_topology_v2 import (
    TopologyCollectionItem,
    TopologyCollectionPlan,
    TopologyCollectionRun,
)
from services.inspection.logic import is_valid_schedule_cron
from services.action_collection import timer as collection_timer
from services.network_topology.canonicalization import canonicalize_json
from services.network_topology.collection.errors import CollectionError
from settings import TASK_TOPIC

BUILTIN_PLAYBOOK_NAME = '网络拓扑自动采集'
TIMER_END = collection_timer.TIMER_END
TIMER_PREFIX = 'network_topology_collection_'
LOCK_NAMESPACE = 'network_topology_collection'
MAX_RESOURCES = 500
PLAN_FIELDS = ('id', 'topology_id', 'enabled', 'schedule_cron', 'concurrency',
               'per_item_timeout_seconds', 'run_timeout_seconds', 'notify_enabled',
               'notify_config', 'timed_id', 'last_run_id', 'consecutive_failures', 'revision')
ITEM_FIELDS = ('id', 'app_name', 'app_version', 'action_func', 'action_input', 'resources',
               'order')


def _invalid(reason):
    return CollectionError('PLAN_INVALID', 400, reason)


def _reject_tenant(value):
    if isinstance(value, dict):
        if {'tenant', 'tenant_id'} & value.keys():
            raise _invalid('请求不得携带租户字段')
        for child in value.values():
            _reject_tenant(child)
    elif isinstance(value, list):
        for child in value:
            _reject_tenant(child)


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise _invalid(f'{name} 必须是 {low}-{high} 的整数')
    return value


def _boolean(raw, key, default=False):
    value = raw.get(key, default)
    if type(value) is not bool:
        raise _invalid(f'{key} 必须是布尔值')
    return value


def validate_plan_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise _invalid('请求必须是对象')
    _reject_tenant(payload)
    if not is_valid_schedule_cron(payload.get('schedule_cron')):
        raise _invalid('采集周期格式非法')
    data = {key: _boolean(payload, key) for key in ('enabled', 'notify_enabled')}
    data['schedule_cron'] = payload['schedule_cron']
    for key, low, high in (('concurrency', 1, 64), ('per_item_timeout_seconds', 30, 3600), ('run_timeout_seconds', 600, 3600)):
        data[key] = _integer(payload.get(key), key, low, high)
    data['expected_revision'] = _integer(payload.get('expected_revision', 0), 'expected_revision', 0, 2**63-1)
    config = payload.get('notify_config')
    if config is not None and not isinstance(config, dict):
        raise _invalid('通知配置必须是对象')
    data['notify_config'] = deepcopy(config)
    raw_items = payload.get('items', [])
    if not isinstance(raw_items, list):
        raise _invalid('采集项必须是数组')
    items, seen, selected_ids = [], set(), set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise _invalid('采集项必须是对象')
        app, func = raw.get('app_name'), raw.get('action_func')
        if not isinstance(app, str) or not app.strip() or len(app.strip()) > 255 or not isinstance(func, str) or not func.strip() or len(func.strip()) > 255:
            raise _invalid('采集项必须指定应用和动作')
        app, func = app.strip(), func.strip()
        action_input = raw.get('action_input', {})
        if not isinstance(action_input, dict):
            raise _invalid('动作参数必须是对象')
        try:
            key = (app, func, canonicalize_json(action_input))
        except (ValueError, TypeError) as exc:
            raise _invalid('动作参数不是有效 JSON') from exc
        if key in seen:
            raise _invalid('存在重复的采集项')
        seen.add(key)
        resources = raw.get('resources', [])
        if not isinstance(resources, list):
            raise _invalid('资源必须是数组')
        normalized, item_seen = [], set()
        for entry in resources:
            if not isinstance(entry, dict):
                raise _invalid('资源必须是对象')
            rid = entry.get('resource_id')
            if not isinstance(rid, str) or not rid.strip() or len(rid.strip()) > 36:
                raise _invalid('资源 ID 非法')
            rid = rid.strip()
            selected_ids.add(rid)
            if rid not in item_seen:
                normalized.append(dict(resource_id=rid))
                item_seen.add(rid)
        if not normalized:
            raise _invalid('采集项至少选择一台资源')
        version = raw.get('app_version')
        if version is not None and (not isinstance(version, str) or len(version) > 50):
            raise _invalid('应用版本非法')
        items.append(dict(app_name=app, app_version=version or None, action_func=func,
                          action_input=deepcopy(action_input), resources=normalized,
                          include_new_resources=False, enabled=True,
                          order=_integer(raw.get('order', index), 'order', 0, 2**31-1)))
    if len(selected_ids) > MAX_RESOURCES:
        raise _invalid(f'采集设备总数不能超过 {MAX_RESOURCES}')
    if data['enabled'] and not items:
        raise _invalid('定时采集至少需要一个采集项')
    data['items'] = items
    return data


async def builtin_playbook():
    return await collection_timer.builtin_playbook(BUILTIN_PLAYBOOK_NAME)


async def get_plan(tenant_id: str, topology_id: str) -> dict:
    plan = await TopologyCollectionPlan.filter(tenant_id=tenant_id, topology_id=topology_id, deleted=False).first()
    if plan is None:
        return {'plan': None, 'items': []}
    items = await TopologyCollectionItem.filter(tenant_id=tenant_id, plan_id=plan.id, deleted=False).order_by('order', 'id')
    return {'plan': {key: deepcopy(getattr(plan, key)) for key in PLAN_FIELDS},
            'items': [{**{key: deepcopy(getattr(item, key)) for key in ITEM_FIELDS if key != 'resources'},
                       'resources': [{'resource_id': str(link['resource_id'])}
                                     for link in item.resources or []]} for item in items]}


async def collection_summaries(tenant_id: str, topology_ids: list[str]) -> dict[str, dict]:
    """Return one collection summary per topology using two batched queries."""
    topology_ids = [str(value) for value in topology_ids if value]
    if not topology_ids:
        return {}
    plan_query = TopologyCollectionPlan.filter(
        tenant_id=tenant_id,
        topology_id__in=topology_ids,
        deleted=False,
    )
    # Collection items have no ORM relation to plans. Count configured items
    # in the same plan query so list pages stay at two database queries.
    try:
        plan_query = plan_query.annotate(
            enabled_item_count=RawSQL(
                '(SELECT COUNT(*) FROM "network_topology_collection_item" AS item '
                'WHERE item."plan_id" = "network_topology_collection_plan"."id" '
                'AND item."tenant_id" = "network_topology_collection_plan"."tenant_id" '
                'AND item."deleted" = FALSE)'
            )
        )
    except AttributeError:
        # Small repository fakes used by direct-handler tests need only support
        # filter/await; they may expose ``items`` on each plan instead.
        pass
    plans = await plan_query
    last_run_ids = [str(plan.last_run_id) for plan in plans if getattr(plan, "last_run_id", None)]
    from tortoise.expressions import Q
    from models.network_topology_v2 import RUN_TERMINAL_STATES
    runs = await TopologyCollectionRun.filter(
        Q(id__in=last_run_ids) | Q(topology_id__in=topology_ids, state__not_in=list(RUN_TERMINAL_STATES)),
        tenant_id=tenant_id, deleted=False,
    )
    runs_by_id = {str(run.id): run for run in runs}
    active_by_topology = {str(run.topology_id): run for run in runs if run.state not in RUN_TERMINAL_STATES}
    summaries = {}
    for plan in plans:
        # Older fakes and integrations may expose item rows on the plan. The
        # ORM model stores them separately; an enabled plan is the only signal
        # available to the list query in that case.
        items = getattr(plan, "items", None)
        enabled_item_count = getattr(plan, "enabled_item_count", None)
        has_enabled_item = (
            bool(enabled_item_count)
            if enabled_item_count is not None
            else
            bool(items)
            if items is not None
            else bool(getattr(plan, "enabled", False))
        )
        if not has_enabled_item and str(plan.topology_id) not in active_by_topology:
            continue
        last_run = active_by_topology.get(str(plan.topology_id)) or (
            runs_by_id.get(str(plan.last_run_id)) if getattr(plan, "last_run_id", None) else None
        )
        last_run_payload = None
        if last_run is not None:
            finished_at = getattr(last_run, "finished_at", None)
            if finished_at is not None and hasattr(finished_at, "isoformat"):
                finished_at = finished_at.isoformat()
            last_run_payload = {
                "id": str(last_run.id),
                "state": getattr(last_run, "state", None),
                "result": getattr(last_run, "result", None),
                "finished_at": finished_at,
                "error_code": getattr(last_run, "error_code", None),
            }
        summaries[str(plan.topology_id)] = {
            "enabled": bool(plan.enabled),
            "schedule_cron": plan.schedule_cron,
            "last_run": last_run_payload,
        }
    return summaries


async def save_plan(tenant_id: str, topology_id: str, payload: dict, *, account_id: str) -> dict:
    data = validate_plan_payload(payload)
    # Lock the owning topology as well: SELECT FOR UPDATE on a missing plan cannot
    # serialize simultaneous first creation.
    from models import NetworkTopology
    async with in_transaction() as db:
        topology = await NetworkTopology.filter(id=topology_id, tenant_id=tenant_id, deleted=False).using_db(db).select_for_update().first()
        if topology is None:
            raise CollectionError('TOPOLOGY_NOT_FOUND', 404)
        plan = await TopologyCollectionPlan.filter(tenant_id=tenant_id, topology_id=topology_id, deleted=False).using_db(db).select_for_update().first()
        if data['expected_revision'] != (plan.revision if plan else 0):
            raise CollectionError('PLAN_REVISION_CONFLICT', 409)
        for item in data['items']:
            filters = dict(tenant_id=tenant_id, deleted=False, tool_name=item['app_name'])
            if item['app_version']:
                filters['version_number'] = item['app_version']
            actions = await ToolAction.filter(**filters).using_db(db)
            if not any(isinstance(action.action, dict) and action.action.get('func') == item['action_func'] for action in actions):
                raise _invalid('采集动作不存在')
            for resource in item['resources']:
                if not await ToolResources.filter(id=resource['resource_id'], tenant_id=tenant_id, deleted=False, tool_name=item['app_name'], internal=False).using_db(db).exists():
                    raise _invalid('应用资源不存在或不可用')
        if data['enabled']:
            pb, pv = await builtin_playbook()
            if pb is None or pv is None:
                raise CollectionError('COLLECTION_PLAYBOOK_UNAVAILABLE', 409)
        values = {k: v for k, v in data.items() if k not in ('items', 'expected_revision')}
        if plan is None:
            plan = await TopologyCollectionPlan.create(using_db=db, tenant_id=tenant_id, topology_id=topology_id, revision=1, updater=account_id, **values)
        else:
            for key, value in values.items():
                setattr(plan, key, value)
            plan.revision += 1
            plan.updater = account_id
            await plan.save(using_db=db)
        await TopologyCollectionItem.filter(tenant_id=tenant_id, plan_id=plan.id, deleted=False).using_db(db).update(deleted=True)
        for item in data['items']:
            await TopologyCollectionItem.create(using_db=db, tenant_id=tenant_id, plan_id=plan.id, **item)
    await sync_timer(plan)
    return await get_plan(tenant_id, topology_id)


@asynccontextmanager
async def _locked_plan(plan):
    async with collection_timer.locked_plan(TopologyCollectionPlan)(plan) as current:
        yield current


def _timer_serialization(plan):
    return collection_timer.advisory_lock(LOCK_NAMESPACE, plan.tenant_id, plan.id)


async def sync_timer(plan) -> None:
    async with _timer_serialization(plan):
        await _reconcile_timer(plan)


async def _reconcile_timer(plan) -> None:
    # Resolve module globals per call to preserve topology's patchable API.
    await collection_timer.reconcile_timer_locked(
        plan, timer_prefix=TIMER_PREFIX, locked_plan=_locked_plan,
        playbook_lookup=builtin_playbook, timed_model=Timed, produce=aproduce,
    )


async def _prepare_timer_locked(plan):
    return await collection_timer.prepare_timer_locked(
        plan, timer_prefix=TIMER_PREFIX,
        playbook_lookup=builtin_playbook, timed_model=Timed,
    )


async def delete_plan_for_topology(tenant_id, topology_id) -> None:
    plan = await TopologyCollectionPlan.filter(tenant_id=tenant_id, topology_id=topology_id, deleted=False).first()
    if plan is None:
        return
    async with _timer_serialization(plan):
        async with _locked_plan(plan) as current:
            if current is None:
                return
            current.enabled = False
            await current.save(update_fields=['enabled'])
            prepared = await _prepare_timer_locked(current)
        # Removal must also be outside the transaction containing retry state.
        if prepared:
            await aproduce(TASK_TOPIC, prepared[1], raise_on_error=True)
        async with _locked_plan(plan) as current:
            if current is None:
                return
            await TopologyCollectionItem.filter(tenant_id=tenant_id, plan_id=plan.id, deleted=False).update(deleted=True)
            await TopologyCollectionPlan.filter(tenant_id=tenant_id, id=plan.id, deleted=False).update(deleted=True, enabled=False)
