"""Tenant-scoped collection plans and retryable internal timer reconciliation."""
from __future__ import annotations

from copy import deepcopy
from contextlib import asynccontextmanager

from tortoise.transactions import in_transaction
from core.maintenance import DEFAULT_TENANT_ID
from libs.kafka import aproduce
from models import AssetInstance, Playbook, PlaybookVersion, Timed, ToolAction, ToolResources
from models.network_topology_v2 import TopologyCollectionItem, TopologyCollectionPlan
from services.inspection.logic import is_valid_schedule_cron
from services.network_topology.canonicalization import canonicalize_json
from services.network_topology.collection.errors import CollectionError
from settings import TASK_TOPIC

BUILTIN_PLAYBOOK_NAME = '网络拓扑自动采集'
TIMER_END = '2099-12-31 23:59:59'
MAX_RESOURCES = 500
PLAN_FIELDS = ('id', 'topology_id', 'enabled', 'schedule_cron', 'concurrency',
               'per_item_timeout_seconds', 'run_timeout_seconds', 'notify_enabled',
               'notify_config', 'timed_id', 'last_run_id', 'consecutive_failures', 'revision')
ITEM_FIELDS = ('id', 'app_name', 'app_version', 'action_func', 'action_input', 'resources',
               'include_new_resources', 'enabled', 'order')


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
    items, seen, links = [], set(), {}
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
            rid, asset = entry.get('resource_id'), entry.get('asset_instance_id')
            if not isinstance(rid, str) or not rid.strip() or len(rid.strip()) > 36:
                raise _invalid('资源 ID 非法')
            rid = rid.strip()
            if asset is not None and (not isinstance(asset, str) or not asset.strip() or len(asset.strip()) > 36):
                raise _invalid('资产 ID 非法')
            asset = asset.strip() if asset is not None else None
            if rid in links and links[rid] != asset:
                raise _invalid('同一资源关联了不同资产')
            links[rid] = asset
            if rid not in item_seen:
                normalized.append(dict(resource_id=rid, asset_instance_id=asset))
                item_seen.add(rid)
        include = _boolean(raw, 'include_new_resources')
        if not normalized and not include:
            raise _invalid('采集项至少选择一台资源')
        version = raw.get('app_version')
        if version is not None and (not isinstance(version, str) or len(version) > 50):
            raise _invalid('应用版本非法')
        items.append(dict(app_name=app, app_version=version or None, action_func=func,
                          action_input=deepcopy(action_input), resources=normalized,
                          include_new_resources=include, enabled=_boolean(raw, 'enabled', True),
                          order=_integer(raw.get('order', index), 'order', 0, 2**31-1)))
    if len(links) > MAX_RESOURCES:
        raise _invalid(f'采集设备总数不能超过 {MAX_RESOURCES}')
    data['items'] = items
    return data


async def builtin_playbook():
    playbook = await Playbook.filter(name=BUILTIN_PLAYBOOK_NAME, tenant_id=DEFAULT_TENANT_ID, deleted=False, internal=True).first()
    if not playbook:
        return None, None
    version = await PlaybookVersion.filter(playbook_id=playbook.id, tenant_id=DEFAULT_TENANT_ID, deleted=False, status=1).order_by('-utime').first()
    return playbook, version


async def get_plan(tenant_id: str, topology_id: str) -> dict:
    plan = await TopologyCollectionPlan.filter(tenant_id=tenant_id, topology_id=topology_id, deleted=False).first()
    if plan is None:
        return {'plan': None, 'items': []}
    items = await TopologyCollectionItem.filter(tenant_id=tenant_id, plan_id=plan.id, deleted=False).order_by('order', 'id')
    return {'plan': {key: deepcopy(getattr(plan, key)) for key in PLAN_FIELDS},
            'items': [{key: deepcopy(getattr(item, key)) for key in ITEM_FIELDS} for item in items]}


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
                if resource['asset_instance_id'] and not await AssetInstance.filter(id=resource['asset_instance_id'], tenant_id=tenant_id, deleted=False).using_db(db).exists():
                    raise _invalid('关联资产不存在')
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
    async with in_transaction() as db:
        current = await TopologyCollectionPlan.filter(
            id=plan.id, tenant_id=plan.tenant_id, deleted=False,
        ).using_db(db).select_for_update().first()
        yield current


async def sync_timer(plan) -> None:
    # Re-read under the plan row lock: older post-commit callers must reconcile
    # the latest revision, and two callers must not create separate timer rows.
    prepared = None
    async with _locked_plan(plan) as current:
        if current is not None:
            prepared = await _prepare_timer_locked(current)
    if prepared is None:
        return
    timed, message, enabled, cron = prepared
    # The identity and disabled state are committed before touching Kafka.  A
    # broker failure therefore cannot roll back the row needed for retry.
    await aproduce(TASK_TOPIC, message, raise_on_error=True)
    async with _locked_plan(plan) as current:
        if current is None:
            return
        # A newer save may have changed the schedule while Kafka was in flight;
        # leave the timer disabled and let that save's reconciliation win.
        if bool(current.enabled) != enabled or current.schedule_cron != cron:
            return
        timed.enable = int(enabled)
        timed.tactics = cron
        await timed.save()
        current.timed_id = timed.id
        await current.save(update_fields=['timed_id'])


async def _prepare_timer_locked(plan):
    playbook = version = None
    if plan.enabled:
        playbook, version = await builtin_playbook()
        if playbook is None or version is None:
            raise CollectionError('COLLECTION_PLAYBOOK_UNAVAILABLE', 409)
    timed = await Timed.filter(name=f'network_topology_collection_{plan.id}', tenant_id=DEFAULT_TENANT_ID, deleted=False, internal=True).first()
    if timed is None and not plan.enabled:
        return
    created = timed is None
    if created:
        timed = await Timed.create(name=f'network_topology_collection_{plan.id}', tenant_id=DEFAULT_TENANT_ID,
            type=0, playbook=playbook, playbook_version=version.version, playbook_input={}, status=1,
            tactics=plan.schedule_cron, end_time=TIMER_END, enable=0, creator_id=plan.updater,
            updator_id=plan.updater, internal=True)
    inputs = ({'flow_id': version.id, 'flow_input': {'plan_id': plan.id, 'collection_run_id': ''},
               'executor': plan.updater} if plan.enabled else timed.playbook_input)
    msg = dict(run_type='flow', run_args=inputs, timer='cron' if plan.enabled else 'remove',
               timer_rule=dict(end_date=TIMER_END, crontab=plan.schedule_cron) if plan.enabled else {},
               timer_id=timed.id, tenant_id=DEFAULT_TENANT_ID)
    timed.enable = 0
    timed.tactics = plan.schedule_cron
    timed.playbook_input = inputs
    timed.end_time = TIMER_END
    timed.updator_id = plan.updater
    if plan.enabled:
        timed.playbook_id = playbook.id
        timed.playbook_version = version.version
    await timed.save()
    plan.timed_id = timed.id
    await plan.save(update_fields=['timed_id'])
    return timed, msg, bool(plan.enabled), plan.schedule_cron


async def delete_plan_for_topology(tenant_id, topology_id) -> None:
    async with in_transaction() as db:
        plan = await TopologyCollectionPlan.filter(tenant_id=tenant_id, topology_id=topology_id, deleted=False).using_db(db).select_for_update().first()
        if plan is None:
            return
        plan.enabled = False
        prepared = await _prepare_timer_locked(plan)
        if prepared:
            await aproduce(TASK_TOPIC, prepared[1], raise_on_error=True)
            prepared[0].enable = 0
            await prepared[0].save()
        await TopologyCollectionItem.filter(tenant_id=tenant_id, plan_id=plan.id, deleted=False).using_db(db).update(deleted=True)
        await TopologyCollectionPlan.filter(tenant_id=tenant_id, id=plan.id, deleted=False).using_db(db).update(deleted=True, enabled=False)
