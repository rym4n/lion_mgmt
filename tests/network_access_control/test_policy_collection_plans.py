# tests/network_access_control/test_policy_collection_plans.py
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.action_collection.errors import CollectionError
from services.network_access_control.collection import limits, plans


def payload(**kw):
    data = dict(expected_revision=0, enabled=True, schedule_cron="0 0 3 * * *", concurrency=8,
                per_item_timeout_seconds=300, run_timeout_seconds=3600, notify_enabled=False,
                notify_config=None,
                items=[dict(app_name="huawei_usg", action_func="collect_policy_observations",
                            action_input={}, resources=[dict(resource_id="r1", device_asset_id="a1")])])
    data.update(kw)
    return data


def test_valid_payload_keeps_device_binding():
    item = plans.validate_plan_payload(payload())["items"][0]
    assert item["resources"] == [{"resource_id": "r1", "device_asset_id": "a1"}]
    unbound = payload(items=[dict(app_name="x", action_func="y", resources=[dict(resource_id="r2")])])
    assert plans.validate_plan_payload(unbound)["items"][0]["resources"] == [
        {"resource_id": "r2", "device_asset_id": None}]


def test_save_rejects_resource_in_two_items():
    items = [dict(app_name="huawei_usg", action_func="collect_policy_observations",
                  resources=[dict(resource_id="r1")]),
             dict(app_name="huawei_usg", action_func="collect_policy_observations",
                  action_input={"vsys": "b"}, resources=[dict(resource_id="r1")])]
    with pytest.raises(CollectionError) as exc:
        plans.validate_plan_payload(payload(items=items))
    assert exc.value.code == "PLAN_INVALID"
    assert exc.value.message == "同一资源只能出现在一个采集项中"


def test_duplicate_resource_in_one_item_is_deduplicated():
    items = [dict(app_name="a", action_func="b",
                  resources=[dict(resource_id="r1", device_asset_id="a1"), dict(resource_id="r1")])]
    assert plans.validate_plan_payload(payload(items=items))["items"][0]["resources"] == [
        {"resource_id": "r1", "device_asset_id": "a1"}]


@pytest.mark.parametrize("bad", [
    {"tenant_id": "x"},
    {"schedule_cron": "bad"},
    {"concurrency": 0},
    {"run_timeout_seconds": 3601},
    {"items": [dict(app_name="a", action_func="b", resources=[])]},
    {"items": [dict(app_name="a", action_func="b", resources=[dict(resource_id="r", device_asset_id=7)])]},
])
def test_invalid_payloads(bad):
    with pytest.raises(CollectionError) as exc:
        plans.validate_plan_payload(payload(**bad))
    assert exc.value.code == "PLAN_INVALID"


def test_resource_limit():
    items = [dict(app_name="a", action_func="b",
                  resources=[dict(resource_id=f"r{i}") for i in range(501)])]
    with pytest.raises(CollectionError):
        plans.validate_plan_payload(payload(items=items))


class FakePlanRepository:
    def __init__(self, *, bad_asset=False):
        self.plan = None
        self.items = []
        self.bad_asset = bad_asset

    async def load(self, tenant_id):
        return self.plan, self.items

    async def check_references(self, tenant_id, items):
        if self.bad_asset:
            raise CollectionError("PLAN_INVALID", 400, "绑定的设备资产不存在或不是网络设备")

    async def upsert(self, tenant_id, data, *, account_id):
        revision = self.plan.revision if self.plan else 0
        if data["expected_revision"] != revision:
            raise CollectionError("PLAN_REVISION_CONFLICT", 409)
        values = {k: v for k, v in data.items() if k not in ("items", "expected_revision")}
        self.plan = SimpleNamespace(id="p1", tenant_id=tenant_id, revision=revision + 1, last_run_id=None,
                                    consecutive_failures=0, timed_id=None, updater=account_id, **values)
        self.items = [SimpleNamespace(id=f"i{n}", **item) for n, item in enumerate(data["items"])]
        return self.plan


@pytest.mark.asyncio
async def test_save_checks_asset_binding(monkeypatch):
    monkeypatch.setattr(plans.collection_timer, "builtin_playbook", AsyncMock(return_value=("pb", "pv")))
    with pytest.raises(CollectionError) as exc:
        await plans.save_plan("t1", payload(), account_id="acc", repository=FakePlanRepository(bad_asset=True))
    assert exc.value.message == "绑定的设备资产不存在或不是网络设备"


@pytest.mark.asyncio
async def test_save_requires_playbook_when_enabled(monkeypatch):
    monkeypatch.setattr(plans.collection_timer, "builtin_playbook", AsyncMock(return_value=(None, None)))
    with pytest.raises(CollectionError) as exc:
        await plans.save_plan("t1", payload(), account_id="acc", repository=FakePlanRepository())
    assert exc.value.code == "COLLECTION_PLAYBOOK_UNAVAILABLE"


@pytest.mark.asyncio
async def test_save_reconciles_timer_and_returns_projection(monkeypatch):
    monkeypatch.setattr(plans.collection_timer, "builtin_playbook", AsyncMock(return_value=("pb", "pv")))
    reconcile = AsyncMock()
    monkeypatch.setattr(plans.collection_timer, "reconcile_timer", reconcile)
    monkeypatch.setattr(plans, "_last_run", AsyncMock(return_value=None))
    repo = FakePlanRepository()
    result = await plans.save_plan("t1", payload(), account_id="acc", repository=repo)
    kwargs = reconcile.await_args.kwargs
    assert kwargs["timer_prefix"] == "network_policy_collection_"
    assert kwargs["playbook_name"] == "网络策略自动采集"
    assert kwargs["lock_namespace"] == "network_policy_collection"
    assert result["plan"]["revision"] == 1 and "timed_id" not in result["plan"]
    assert result["items"][0]["resources"] == [{"resource_id": "r1", "device_asset_id": "a1"}]
    with pytest.raises(CollectionError) as exc:
        await plans.save_plan("t1", payload(expected_revision=0), account_id="acc", repository=repo)
    assert exc.value.code == "PLAN_REVISION_CONFLICT"


@pytest.mark.asyncio
async def test_get_plan_defaults_when_missing(monkeypatch):
    monkeypatch.setattr(plans.collection_timer, "builtin_playbook", AsyncMock(return_value=(None, None)))
    result = await plans.get_plan("t1", repository=FakePlanRepository())
    assert result["plan"]["revision"] == 0 and result["plan"]["id"] is None
    assert result["plan"]["schedule_cron"] == "0 0 3 * * *"
    assert result["items"] == [] and result["last_run"] is None
    assert result["playbook_available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("stored,expected", [(None, 20000), ("5000", 5000), ("0", 20000), ("abc", 20000),
                                             ("100001", 20000)])
async def test_max_rules_per_device(monkeypatch, stored, expected):
    row = None if stored is None else SimpleNamespace(value=stored)
    class Query:
        async def first(self):
            return row
    monkeypatch.setattr(limits.Config, "filter", lambda **kw: Query())
    assert await limits.max_rules_per_device() == expected


@pytest.mark.parametrize('bad', [
    {'enabled': 1}, {'notify_enabled': 'true'}, {'concurrency': True},
    {'per_item_timeout_seconds': 29}, {'expected_revision': -1},
    {'notify_config': []}, {'items': 'bad'},
    {'items': [dict(app_name='a', action_func='b', action_input=[], resources=[dict(resource_id='r')])]},
    {'items': [dict(app_name='a', action_func='b', action_input={'tenant_id': 'x'}, resources=[dict(resource_id='r')])]},
    {'items': [dict(app_name='a', action_func='b', action_input={'x': float('nan')}, resources=[dict(resource_id='r')])]},
])
def test_invalid_payload_types_and_nested_tenant(bad):
    with pytest.raises(CollectionError) as exc:
        plans.validate_plan_payload(payload(**bad))
    assert exc.value.code == 'PLAN_INVALID'


def test_duplicate_item_uses_canonical_json_and_input_is_not_mutated():
    item = dict(app_name='a', action_func='b', action_input={'a': 1, 'b': 2}, resources=[dict(resource_id='r')])
    other = dict(item, action_input={'b': 2, 'a': 1}, resources=[dict(resource_id='other')])
    with pytest.raises(CollectionError) as exc:
        plans.validate_plan_payload(payload(items=[item, other]))
    assert exc.value.message == '存在重复的采集项'
    result = plans.validate_plan_payload(payload(items=[item]))
    result['items'][0]['action_input']['a'] = 3
    assert item['action_input']['a'] == 1


@pytest.mark.asyncio
async def test_disabled_empty_plan_can_save_without_playbook(monkeypatch):
    monkeypatch.setattr(plans.collection_timer, 'builtin_playbook', AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(plans.collection_timer, 'reconcile_timer', AsyncMock())
    result = await plans.save_plan('t1', payload(enabled=False, items=[]), account_id='acc', repository=FakePlanRepository())
    assert result['plan']['enabled'] is False
    assert result['items'] == []
    assert result['playbook_available'] is False


class Query:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.order = None
        self.db = None
        self.locked = False
        self.update = AsyncMock()

    async def first(self):
        return self.rows[0] if self.rows else None

    async def exists(self):
        return bool(self.rows)

    def order_by(self, *fields):
        self.order = fields
        return self

    def using_db(self, db):
        self.db = db
        return self

    def select_for_update(self):
        self.locked = True
        return self

    def __await__(self):
        async def result():
            return self.rows
        return result().__await__()


@pytest.mark.asyncio
async def test_repository_load_is_tenant_scoped_and_ordered(monkeypatch):
    from unittest.mock import Mock
    plan = SimpleNamespace(id='p1')
    plan_filter = Mock(return_value=Query([plan]))
    item_query = Query([SimpleNamespace(id='i1')])
    item_filter = Mock(return_value=item_query)
    monkeypatch.setattr(plans.NetworkAccessPolicyCollectionPlan, 'filter', plan_filter)
    monkeypatch.setattr(plans.NetworkAccessPolicyCollectionItem, 'filter', item_filter)
    loaded, items = await plans.OrmPlanRepository().load('t1')
    assert loaded is plan and items[0].id == 'i1'
    plan_filter.assert_called_once_with(tenant_id='t1', deleted=False)
    item_filter.assert_called_once_with(tenant_id='t1', plan_id='p1', deleted=False)
    assert item_query.order == ('order', 'id')


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', [None, 'action', 'resource', 'asset', 'asset_kind'])
async def test_repository_checks_live_tenant_resource_and_asset_kind(monkeypatch, invalid):
    from unittest.mock import Mock
    actions = [] if invalid == 'action' else [SimpleNamespace(action={'func': 'collect_policy_observations'})]
    action_filter = Mock(return_value=Query(actions))
    resource_filter = Mock(return_value=Query([] if invalid == 'resource' else [object()]))
    asset = SimpleNamespace(asset_model_id='synthetic-model', asset_model_key='server' if invalid == 'asset_kind' else 'firewall')
    asset_filter = Mock(return_value=Query([] if invalid == 'asset' else [asset]))
    for cls, mocked in ((plans.ToolAction, action_filter), (plans.ToolResources, resource_filter),
                        (plans.AssetInstance, asset_filter)):
        monkeypatch.setattr(cls, 'filter', mocked)
    data = plans.validate_plan_payload(payload())
    repo = plans.OrmPlanRepository()
    if invalid:
        with pytest.raises(CollectionError) as exc:
            await repo.check_references('t1', data['items'])
        assert exc.value.code == 'PLAN_INVALID'
    else:
        await repo.check_references('t1', data['items'])
        action_filter.assert_called_once_with(tenant_id='t1', deleted=False, tool_name='huawei_usg')
        resource_filter.assert_called_once_with(id='r1', tenant_id='t1', deleted=False, tool_name='huawei_usg', internal=False)
        asset_filter.assert_called_once_with(id='a1', tenant_id='t1', deleted=False)


@pytest.mark.asyncio
@pytest.mark.parametrize('existing_revision,expected_revision', [(None, 0), (2, 2), (2, 1)])
async def test_repository_atomic_upsert_revision_and_tenant(monkeypatch, existing_revision, expected_revision):
    from contextlib import asynccontextmanager
    from unittest.mock import Mock
    db = object()
    @asynccontextmanager
    async def transaction():
        yield db
    monkeypatch.setattr(plans, 'in_transaction', transaction)
    existing = None if existing_revision is None else SimpleNamespace(id='p1', revision=existing_revision, save=AsyncMock())
    query = Query([] if existing is None else [existing])
    plan_filter = Mock(return_value=query)
    create = AsyncMock(return_value=SimpleNamespace(id='p1', revision=1))
    item_query = Query()
    item_filter = Mock(return_value=item_query)
    item_create = AsyncMock()
    monkeypatch.setattr(plans.NetworkAccessPolicyCollectionPlan, 'filter', plan_filter)
    monkeypatch.setattr(plans.NetworkAccessPolicyCollectionPlan, 'create', create)
    monkeypatch.setattr(plans.NetworkAccessPolicyCollectionItem, 'filter', item_filter)
    monkeypatch.setattr(plans.NetworkAccessPolicyCollectionItem, 'create', item_create)
    data = plans.validate_plan_payload(payload(expected_revision=expected_revision))
    if existing_revision is not None and existing_revision != expected_revision:
        with pytest.raises(CollectionError) as exc:
            await plans.OrmPlanRepository().upsert('t1', data, account_id='acc')
        assert exc.value.code == 'PLAN_REVISION_CONFLICT'
        item_query.update.assert_not_awaited()
        item_create.assert_not_awaited()
    else:
        result = await plans.OrmPlanRepository().upsert('t1', data, account_id='acc')
        assert result.revision == (existing_revision or 0) + 1
        item_filter.assert_called_once_with(tenant_id='t1', plan_id='p1', deleted=False)
        item_query.update.assert_awaited_once_with(deleted=True)
        assert item_create.await_args.kwargs['tenant_id'] == 't1'
        assert item_create.await_args.kwargs['using_db'] is db
        if existing is None:
            assert create.await_args.kwargs['tenant_id'] == 't1'
            assert create.await_args.kwargs['updater'] == 'acc'
        else:
            existing.save.assert_awaited_once_with(using_db=db)
            assert existing.updater == 'acc'
    plan_filter.assert_called_once_with(tenant_id='t1', deleted=False)
    assert query.locked and query.db is db


@pytest.mark.asyncio
async def test_repository_create_race_maps_integrity_error(monkeypatch):
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def transaction():
        yield object()
    monkeypatch.setattr(plans, 'in_transaction', transaction)
    monkeypatch.setattr(plans.NetworkAccessPolicyCollectionPlan, 'filter', lambda **kw: Query())
    monkeypatch.setattr(plans.NetworkAccessPolicyCollectionPlan, 'create', AsyncMock(side_effect=plans.IntegrityError('duplicate')))
    with pytest.raises(CollectionError) as exc:
        await plans.OrmPlanRepository().upsert('t1', plans.validate_plan_payload(payload()), account_id='acc')
    assert exc.value.code == 'PLAN_REVISION_CONFLICT'
