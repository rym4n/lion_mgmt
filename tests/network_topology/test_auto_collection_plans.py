from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection import plans


def payload(**kw):
    data = dict(expected_revision=0, enabled=True, schedule_cron='0 0 2 * * *', concurrency=8,
                per_item_timeout_seconds=300, run_timeout_seconds=3600, notify_enabled=False,
                notify_config=None, items=[dict(app_name='switch', action_func='collect', action_input={},
                resources=[dict(resource_id='r1')])])
    data.update(kw)
    return data


def test_valid():
    item = plans.validate_plan_payload(payload())['items'][0]
    assert item['resources'] == [{'resource_id': 'r1'}]
    assert item['include_new_resources'] is False
    assert item['enabled'] is True


def test_legacy_management_fields_do_not_change_selected_resources():
    raw = payload()['items'][0]
    raw.update(include_new_resources=True, enabled=False,
               resources=[{'resource_id': 'r1', 'asset_instance_id': 'a1'}])
    item = plans.validate_plan_payload(payload(items=[raw]))['items'][0]
    assert item['resources'] == [{'resource_id': 'r1'}]
    assert item['include_new_resources'] is False
    assert item['enabled'] is True


def test_item_requires_selected_resource_even_with_legacy_expansion_flag():
    raw = payload()['items'][0]
    raw.update(resources=[], include_new_resources=True)
    with pytest.raises(CollectionError) as exc:
        plans.validate_plan_payload(payload(items=[raw]))
    assert exc.value.code == 'PLAN_INVALID'
    assert exc.value.message == '采集项至少选择一台资源'


def test_scheduled_collection_requires_an_item():
    with pytest.raises(CollectionError) as exc:
        plans.validate_plan_payload(payload(items=[]))
    assert exc.value.code == 'PLAN_INVALID'
    assert exc.value.message == '定时采集至少需要一个采集项'


@pytest.mark.parametrize('kw', [dict(concurrency=0), dict(concurrency=True), dict(run_timeout_seconds=7200),
    dict(schedule_cron='* * * * *'), dict(tenant_id='t'), dict(expected_revision=-1), dict(items='bad'),
    dict(items=[{'tenant':'x'}])])
def test_invalid(kw):
    with pytest.raises(CollectionError) as exc:
        plans.validate_plan_payload(payload(**kw))
    assert exc.value.code == 'PLAN_INVALID'


def test_duplicate_items():
    item = payload()['items'][0]
    with pytest.raises(CollectionError):
        plans.validate_plan_payload(payload(items=[item, item]))


def test_fixed_action_inputs_round_trip_validation():
    data = payload()
    data['items'][0]['action_input'] = {'password': 'fixed-input', 'nested': {'x': [1, 2]}}
    assert plans.validate_plan_payload(data)['items'][0]['action_input'] == data['items'][0]['action_input']


class Query:
    def __init__(self, rows):
        self.rows = rows
    def using_db(self, db):
        return self
    def select_for_update(self):
        return self
    def order_by(self, *args):
        return self
    async def first(self):
        return self.rows[0] if self.rows else None
    async def exists(self):
        return bool(self.rows)
    async def update(self, **kw):
        for row in self.rows:
            for key, value in kw.items():
                setattr(row, key, value)
    def __await__(self):
        async def result():
            return self.rows
        return result().__await__()


@pytest.fixture
def persistence(monkeypatch):
    from contextlib import asynccontextmanager
    import models
    stores = {}
    for name, module in [('NetworkTopology', models), ('TopologyCollectionPlan', plans),
                         ('TopologyCollectionItem', plans), ('ToolAction', plans),
                         ('ToolResources', plans)]:
        rows = []
        stores[name] = rows
        class Store:
            @classmethod
            def filter(cls, **kw):
                assert 'tenant_id' in kw and kw.get('deleted') is False
                return Query([row for row in cls.rows if all(getattr(row, k, None) == v for k, v in kw.items())])
            @classmethod
            async def create(cls, using_db=None, **kw):
                row = SimpleNamespace(id=f'id-{len(cls.rows)}', deleted=False, timed_id=None,
                                      last_run_id=None, consecutive_failures=0, save=AsyncMock(), **kw)
                cls.rows.append(row)
                return row
        Store.rows = rows
        monkeypatch.setattr(module, name, Store)
    @asynccontextmanager
    async def transaction():
        yield object()
    monkeypatch.setattr(plans, 'in_transaction', transaction)
    monkeypatch.setattr(plans, 'sync_timer', AsyncMock())
    stores['NetworkTopology'].append(SimpleNamespace(id='top',tenant_id='t',deleted=False))
    stores['ToolAction'].append(SimpleNamespace(tool_name='switch',tenant_id='t',deleted=False,action={'func':'collect'}))
    stores['ToolResources'].append(SimpleNamespace(id='r1',tool_name='switch',tenant_id='t',deleted=False,internal=False))
    return stores


@pytest.mark.asyncio
async def test_persistence_cas_replacement_and_tenant_isolation(persistence):
    saved = await plans.save_plan('t', 'top', payload(), account_id='acct')
    assert saved['plan']['revision'] == 1
    assert len(saved['items']) == 1
    assert saved['items'][0]['resources'] == [{'resource_id': 'r1'}]
    assert 'enabled' not in saved['items'][0]
    assert 'include_new_resources' not in saved['items'][0]
    with pytest.raises(CollectionError) as exc:
        await plans.save_plan('t', 'top', payload(), account_id='acct')
    assert exc.value.code == 'PLAN_REVISION_CONFLICT'
    saved = await plans.save_plan('t', 'top', payload(expected_revision=1), account_id='acct')
    assert saved['plan']['revision'] == 2
    assert persistence['TopologyCollectionItem'][0].deleted is True
    assert saved['items'][0]['resources'] == [{'resource_id': 'r1'}]
    assert (await plans.get_plan('other', 'top')) == {'plan': None, 'items': []}
    assert plans.sync_timer.await_count == 2


@pytest.mark.asyncio
async def test_reference_validation_before_writes(persistence):
    persistence['ToolResources'][0].tenant_id = 'other'
    with pytest.raises(CollectionError) as exc:
        await plans.save_plan('t', 'top', payload(), account_id='acct')
    assert exc.value.code == 'PLAN_INVALID'
    assert persistence['TopologyCollectionPlan'] == []


@pytest.mark.asyncio
async def test_save_can_delete_all_collection_items(persistence):
    await plans.save_plan('t', 'top', payload(), account_id='acct')
    saved = await plans.save_plan('t', 'top', payload(expected_revision=1, enabled=False, items=[]), account_id='acct')
    assert saved['items'] == []
    assert all(row.deleted for row in persistence['TopologyCollectionItem'])


@pytest.mark.asyncio
async def test_get_hides_legacy_asset_and_item_flags(persistence):
    saved = await plans.save_plan('t', 'top', payload(), account_id='acct')
    row = persistence['TopologyCollectionItem'][0]
    row.resources = [{'resource_id': 'r1', 'asset_instance_id': 'legacy-asset'}]
    row.include_new_resources = True
    row.enabled = False
    result = await plans.get_plan('t', 'top')
    assert result['items'] == saved['items']
