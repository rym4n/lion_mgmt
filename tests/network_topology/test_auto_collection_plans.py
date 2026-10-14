from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from services.network_topology.collection.errors import CollectionError
from services.network_topology.collection import plans


def payload(**kw):
    data = dict(expected_revision=0, enabled=True, schedule_cron='0 0 2 * * *', concurrency=8,
                per_item_timeout_seconds=300, run_timeout_seconds=3600, notify_enabled=False,
                notify_config=None, items=[dict(app_name='switch', action_func='collect', action_input={},
                resources=[dict(resource_id='r1', asset_instance_id=None)])])
    data.update(kw)
    return data


def test_valid():
    assert plans.validate_plan_payload(payload())['items'][0]['resources'] == [{'resource_id':'r1','asset_instance_id':None}]


@pytest.mark.parametrize('kw', [dict(concurrency=0), dict(concurrency=True), dict(run_timeout_seconds=7200),
    dict(schedule_cron='* * * * *'), dict(tenant_id='t'), dict(expected_revision=-1), dict(items='bad'),
    dict(items=[{'tenant':'x'}])])
def test_invalid(kw):
    with pytest.raises(CollectionError) as exc:
        plans.validate_plan_payload(payload(**kw))
    assert exc.value.code == 'PLAN_INVALID'


def test_duplicate_and_conflicting_links():
    item = payload()['items'][0]
    for items in ([item,item], [item,{**item, 'action_func':'other','resources':[dict(resource_id='r1',asset_instance_id='a')]}]):
        with pytest.raises(CollectionError):
            plans.validate_plan_payload(payload(items=items))


class Timer(SimpleNamespace):
    row = None
    @classmethod
    def filter(cls, **kw):
        assert kw['tenant_id'] == plans.DEFAULT_TENANT_ID and kw['deleted'] is False
        return SimpleNamespace(first=AsyncMock(return_value=cls.row))
    @classmethod
    async def create(cls, **kw):
        cls.row = cls(id='timer', **kw)
        return cls.row
    async def save(self, **kw):
        pass


@pytest.mark.asyncio
async def test_timer_reconciliation(monkeypatch):
    Timer.row = None
    producer = AsyncMock()
    monkeypatch.setattr(plans,'Timed',Timer)
    monkeypatch.setattr(plans,'aproduce',producer)
    monkeypatch.setattr(plans,'builtin_playbook',AsyncMock(return_value=(SimpleNamespace(id='pb'),SimpleNamespace(id='pv',version='1'))))
    plan = SimpleNamespace(id='p',tenant_id='t',enabled=True,schedule_cron='0 0 2 * * *',updater='acct',timed_id=None,save=AsyncMock())
    await plans.sync_timer(plan)
    assert Timer.row.name == 'network_topology_collection_p'
    assert Timer.row.internal is True
    assert Timer.row.playbook_input == dict(flow_id='pv',flow_input=dict(plan_id='p',collection_run_id=''),executor='acct')
    assert producer.call_args.args[1]['timer'] == 'cron'
    plan.schedule_cron='0 0 3 * * *'
    await plans.sync_timer(plan)
    assert Timer.row.tactics == plan.schedule_cron
    plan.enabled=False
    await plans.sync_timer(plan)
    assert Timer.row.enable == 0
    assert producer.call_args.args[1]['timer'] == 'remove'


@pytest.mark.asyncio
async def test_missing_playbook(monkeypatch):
    monkeypatch.setattr(plans,'builtin_playbook',AsyncMock(return_value=(None,None)))
    with pytest.raises(CollectionError) as exc:
        await plans.sync_timer(SimpleNamespace(enabled=True))
    assert exc.value.code == 'COLLECTION_PLAYBOOK_UNAVAILABLE'

@pytest.mark.asyncio
async def test_timer_publish_failure_retains_retry_identity(monkeypatch):
    Timer.row = None
    producer = AsyncMock(side_effect=[RuntimeError('offline'), None])
    monkeypatch.setattr(plans, 'Timed', Timer)
    monkeypatch.setattr(plans, 'aproduce', producer)
    monkeypatch.setattr(plans, 'builtin_playbook', AsyncMock(return_value=(SimpleNamespace(id='pb'), SimpleNamespace(id='pv', version='1'))))
    plan = SimpleNamespace(id='p', tenant_id='t', enabled=True, schedule_cron='0 0 2 * * *', updater='acct', timed_id=None, save=AsyncMock())
    with pytest.raises(RuntimeError):
        await plans.sync_timer(plan)
    original = Timer.row
    assert original.enable == 0
    assert plan.timed_id == "timer"
    await plans.sync_timer(plan)
    assert Timer.row is original
    assert original.enable == 1
    assert producer.call_args_list[0].args[1]['timer_id'] == producer.call_args_list[1].args[1]['timer_id']


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
                         ('ToolResources', plans), ('AssetInstance', plans)]:
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
    monkeypatch.setattr(plans, 'builtin_playbook', AsyncMock(return_value=(object(), object())))
    stores['NetworkTopology'].append(SimpleNamespace(id='top',tenant_id='t',deleted=False))
    stores['ToolAction'].append(SimpleNamespace(tool_name='switch',tenant_id='t',deleted=False,action={'func':'collect'}))
    stores['ToolResources'].append(SimpleNamespace(id='r1',tool_name='switch',tenant_id='t',deleted=False,internal=False))
    return stores


@pytest.mark.asyncio
async def test_persistence_cas_replacement_and_tenant_isolation(persistence):
    saved = await plans.save_plan('t', 'top', payload(), account_id='acct')
    assert saved['plan']['revision'] == 1
    assert len(saved['items']) == 1
    with pytest.raises(CollectionError) as exc:
        await plans.save_plan('t', 'top', payload(), account_id='acct')
    assert exc.value.code == 'PLAN_REVISION_CONFLICT'
    saved = await plans.save_plan('t', 'top', payload(expected_revision=1), account_id='acct')
    assert saved['plan']['revision'] == 2
    assert persistence['TopologyCollectionItem'][0].deleted is True
    assert (await plans.get_plan('other', 'top')) == {'plan': None, 'items': []}
    assert plans.sync_timer.await_count == 2


@pytest.mark.asyncio
async def test_reference_validation_before_writes(persistence):
    persistence['ToolResources'][0].tenant_id = 'other'
    with pytest.raises(CollectionError) as exc:
        await plans.save_plan('t', 'top', payload(), account_id='acct')
    assert exc.value.code == 'PLAN_INVALID'
    assert persistence['TopologyCollectionPlan'] == []


@pytest.fixture(autouse=True)
def timer_lock(monkeypatch):
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def locked(plan):
        yield plan
    monkeypatch.setattr(plans, '_locked_plan', locked)
    @asynccontextmanager
    async def serial(plan):
        yield
    monkeypatch.setattr(plans, '_timer_serialization', serial)

@pytest.mark.asyncio
async def test_concurrent_stale_reconciliation_uses_latest_plan(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    Timer.row = None
    latest = SimpleNamespace(id='p', tenant_id='t', enabled=True, schedule_cron='0 0 4 * * *', updater='acct', timed_id=None, save=AsyncMock())
    stale = SimpleNamespace(**{**vars(latest), 'schedule_cron': '0 0 2 * * *'})
    lock = asyncio.Lock()
    @asynccontextmanager
    async def locked(plan):
        async with lock:
            yield latest
    monkeypatch.setattr(plans, '_locked_plan', locked)
    monkeypatch.setattr(plans, 'Timed', Timer)
    producer = AsyncMock()
    monkeypatch.setattr(plans, 'aproduce', producer)
    monkeypatch.setattr(plans, 'builtin_playbook', AsyncMock(return_value=(SimpleNamespace(id='pb'), SimpleNamespace(id='pv',version='1'))))
    await asyncio.gather(plans.sync_timer(stale), plans.sync_timer(latest))
    assert Timer.row.tactics == latest.schedule_cron
    assert {call.args[1]['timer_id'] for call in producer.call_args_list} == {'timer'}
    assert {call.args[1]['timer_rule']['crontab'] for call in producer.call_args_list} == {latest.schedule_cron}

@pytest.mark.asyncio
async def test_real_kafka_strict_failure_and_stable_timer_retry(monkeypatch):
    from libs import kafka
    Timer.row = None
    failure = [True]
    class Producer:
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def send_and_wait(self, *args, **kw):
            if failure[0]:
                raise RuntimeError('broker offline')
    monkeypatch.setattr(kafka, 'AIOKafkaProducer', Producer)
    monkeypatch.setattr(kafka, '_normalize_flow_task_message', AsyncMock(side_effect=lambda msg: msg))
    monkeypatch.setattr(plans, 'aproduce', kafka.aproduce)
    monkeypatch.setattr(plans, 'Timed', Timer)
    monkeypatch.setattr(plans, 'builtin_playbook', AsyncMock(return_value=(SimpleNamespace(id='pb'), SimpleNamespace(id='pv', version='1'))))
    plan = SimpleNamespace(id='p',tenant_id='t',enabled=True,schedule_cron='0 0 2 * * *',updater='acct',timed_id=None,save=AsyncMock())
    # Preserve legacy callers' behavior; strict timer callers must observe failure.
    await kafka.aproduce('other-topic', {})
    with pytest.raises(RuntimeError, match='broker offline'):
        await plans.sync_timer(plan)
    row = Timer.row
    assert row.enable == 0
    failure[0] = False
    await plans.sync_timer(plan)
    assert Timer.row is row and row.enable == 1
