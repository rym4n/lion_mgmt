"""Real PostgreSQL regression for direct collection scheduling.

Run from lion_mgmt with an explicit test database:
    COLLECTION_TEST_POSTGRES_DSN=... PYTHONPATH=. uv run --locked python \
        tests/action_collection/collection_postgres_regression.py

Creates and drops one random schema. Requires PostgreSQL CREATE SCHEMA privilege.
The action/agent workers and Kafka producer are replaced at external boundaries;
ORM queries, migrations, locks, savepoints, unique indexes and launch claims run.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
import secrets
import re
from unittest.mock import AsyncMock
import asyncpg
from tortoise import Tortoise, connections
from tortoise.backends.base.config_generator import expand_db_url
from tortoise.transactions import in_transaction as real_transaction
from models import Timed
from models.network_access_control import (
    NetworkAccessPolicyCollectionPlan as PolicyPlan,
    NetworkAccessPolicyCollectionRun as PolicyRun,
    NetworkAccessPolicyCollectionItem as PolicyItem,
)
from models.network_topology_v2 import (
    TopologyCollectionPlan as TopologyPlan,
    TopologyCollectionRun as TopologyRun,
    TopologyCollectionItem as TopologyItem,
)
from services.action_collection import scheduler, timer
from services.action_collection.targets import Target
from services.action_collection.errors import CollectionError
from services.network_access_control.collection import (
    runs as policy_runs, pipeline as policy_pipeline,
)
from services.network_topology.collection import (
    runs as topology_runs, pipeline as topology_pipeline,
)

POSTGRES_DSN = os.environ.get('COLLECTION_TEST_POSTGRES_DSN')
SCHEMA = 'collection_regression_' + secrets.token_hex(6)
NOW = datetime.now(timezone.utc).replace(microsecond=0)
CRON = '0 * * * * *'

async def emit(case, **values):
    result = dict(case=case, **values)
    print(json.dumps(result, default=str), flush=True)

async def migrate(db):
    await db.execute_script('CREATE TABLE permissions_menu ("Permissions" JSONB, "utime" TIMESTAMPTZ, "model" VARCHAR(100));')
    for name in ('132_20261014160000_topology_auto_collection', '134_20261020090000_topology_collection_heartbeat', '135_20261020100000_network_policy_collection'):
        migration = importlib.import_module('migrations.models.'+name)
        await db.execute_script(await migration.upgrade(db))
    for table in ('network_topology_collection_plan', 'network_access_policy_collection_plan'):
        cols = await db.execute_query_dict('SELECT column_name FROM information_schema.columns WHERE table_name=$1 AND table_schema=current_schema()', [table])
        assert not {'scheduled_cron', 'next_run_at'} & {c['column_name'] for c in cols}
    await db.execute_script("INSERT INTO network_access_policy_collection_plan (id, tenant_id, enabled, schedule_cron) VALUES ('migration-preserved', 'migration-tenant', FALSE, '0 0 3 * * *');")
    migration = importlib.import_module('migrations.models.136_20261022090000_direct_collection_schedules')
    for _ in range(2):
        async with real_transaction() as tx:
            await tx.execute_script(await migration.upgrade(tx))
    cols = await db.execute_query_dict("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema=current_schema() AND table_name IN ('network_topology_collection_plan','network_access_policy_collection_plan') AND column_name IN ('scheduled_cron','next_run_at') ORDER BY table_name,column_name")
    assert len(cols) == 4 and all(c['is_nullable']=='YES' for c in cols)
    assert {c['data_type'] for c in cols} == {'character varying','timestamp with time zone'}
    preserved = await PolicyPlan.get(id='migration-preserved')
    assert preserved.schedule_cron == '0 0 3 * * *' and preserved.next_run_at is None and preserved.scheduled_cron is None
    for _ in range(2):
        async with real_transaction() as tx:
            await tx.execute_script(await migration.downgrade(tx))
    await db.execute_script(await migration.upgrade(db))
    assert await PolicyPlan.filter(id='migration-preserved').count() == 1
    # Generate only the Timed model, removing unrelated foreign-key constraints.
    sql = db.schema_generator(db)._get_table_sql(Timed, True)['table_creation_string']
    sql = re.sub(r' REFERENCES "[^"]+" \("[^"]+"\) ON DELETE [A-Z ]+', '', sql)
    await db.execute_script(sql)
    await emit('migration136', forward_applications=3, backward_applications=2, preserved_legacy_plan=True, new_columns=cols)


def install_external_boundaries():
    started = {'policy':[], 'topology':[]}
    policy_pipeline.freeze_targets = AsyncMock(return_value=[Target('item','test_app','v1','main.py','collect','collect',{},'resource','fw','127.0.0.1',None)])
    def track_policy(coro, **kwargs):
        started['policy'].append(str(kwargs['run'].id))
        coro.close()
    def track_topology(coro, **kwargs):
        started['topology'].append(str(kwargs['run'].id))
        coro.close()
    policy_pipeline.start_tracked = track_policy
    topology_pipeline.start_tracked = track_topology
    return started

async def fresh_plan(Plan, label, *, due=True):
    values = {'topology_id': 'top-'+label} if Plan is TopologyPlan else {}
    return await Plan.create(tenant_id='tenant-'+label, enabled=True, schedule_cron=CRON, scheduled_cron=CRON,
                             next_run_at=NOW-timedelta(minutes=5) if due else NOW+timedelta(hours=1), **values)

async def mark_done(Run, plan):
    await Run.filter(plan_id=plan.id).update(state='SUCCEEDED')

async def domain_checks(Plan, Run, Item, runs, label, started):
    domain = scheduler.CollectionDomain(Plan, Run, runs.start_scheduled, runs.launch_run, 'unused_', 'test_'+label)
    plan = await fresh_plan(Plan, label+'-parallel')
    before = len(started[label])
    pids = set()
    txids = set()
    ready = asyncio.Event()
    entries = 0
    depths = {}
    @asynccontextmanager
    async def observed_transaction():
        nonlocal entries
        task = asyncio.current_task()
        depth = depths.get(task, 0)
        depths[task] = depth+1
        try:
            async with real_transaction() as db:
                if depth == 0:
                    info = (await db.execute_query_dict('SELECT pg_backend_pid() AS pid, txid_current() AS txid'))[0]
                    pids.add(info['pid']); txids.add(info['txid'])
                    entries += 1
                    if entries == 2: ready.set()
                    await asyncio.wait_for(ready.wait(), 5)
                yield db
        finally:
            depths[task] = depth
    original_transaction = scheduler.in_transaction
    scheduler.in_transaction = observed_transaction
    try:
        await asyncio.wait_for(asyncio.gather(scheduler.tick_domain(domain, now=NOW), scheduler.tick_domain(domain, now=NOW)), 10)
    finally:
        scheduler.in_transaction = original_transaction
    rows = await Run.filter(plan_id=plan.id)
    cursor = (await Plan.get(id=plan.id)).next_run_at
    assert len(pids)==2 and len(txids)==2, (pids,txids)
    assert len(rows)==1 and cursor == scheduler.next_fire_time(CRON,NOW)
    assert len(started[label])-before == 1, started[label]
    assert rows[0].state == ('COLLECTING' if label=='policy' else 'PENDING') and rows[0].heartbeat_at is not None
    await emit(label+'_parallel_ticks', runs=1, starts=1, backend_pids=sorted(pids), transaction_ids=sorted(txids), cursor=cursor)
    # An already-active run consumes the next due slot without a duplicate or failed transaction.
    await Plan.filter(id=plan.id).update(next_run_at=NOW-timedelta(seconds=1))
    await asyncio.wait_for(asyncio.gather(scheduler.tick_domain(domain, now=NOW),scheduler.tick_domain(domain, now=NOW)),10)
    assert await Run.filter(plan_id=plan.id).count()==1
    assert (await Plan.get(id=plan.id)).next_run_at > NOW
    await emit(label+'_active_overlap', runs=1, cursor_advanced=True)
    # Restart between create/commit and launch, even after plan disable.
    await mark_done(Run,plan)
    pending = await runs.start_scheduled(plan.id,launch=False)
    await Plan.filter(id=plan.id).update(enabled=False)
    before = len(started[label])
    await asyncio.wait_for(asyncio.gather(scheduler.tick_domain(domain,now=NOW),scheduler.tick_domain(domain,now=NOW)),10)
    assert len(started[label])-before == 1 and started[label][-1] == pending['collection_run_id']
    recovered = await Run.get(id=pending['collection_run_id'])
    assert recovered.heartbeat_at is not None
    await emit(label+'_pending_recovery', run_id=recovered.id, starts=1, disabled_plan=True)
    await mark_done(Run,plan)
    # Force manual and scheduled preflight reads to see no active run, then race both inserts.
    race = await fresh_plan(Plan,label+'-manual-scheduled',due=False)
    await Item.create(tenant_id=race.tenant_id,plan_id=race.id,app_name='test_app',action_func='collect',resources=[{'resource_id':'resource'}])
    repository_class = runs.OrmPolicyRunRepository if label=='policy' else runs.OrmRunRepository
    class RacingRepository(repository_class):
        def __init__(self): self.entered=0; self.barrier=asyncio.Event()
        async def active_run(self,*args):
            current = await super().active_run(*args)
            self.entered+=1
            if self.entered<=2:
                if self.entered==2: self.barrier.set()
                await asyncio.wait_for(self.barrier.wait(),5)
            return current
    repo = RacingRepository()
    if label=='policy':
        manual = runs.trigger_manual(race.tenant_id,account_id='test-user',repository=repo)
    else:
        manual = runs.trigger_manual(race.tenant_id,race.topology_id,account_id='test-user',repository=repo)
    results = await asyncio.wait_for(asyncio.gather(manual,runs.start_scheduled(race.id,repository=repo),return_exceptions=True),10)
    active = await Run.filter(plan_id=race.id)
    assert len(active)==1, results
    assert all(not isinstance(r,Exception) or isinstance(r,CollectionError) and r.code=='COLLECTION_RUN_ACTIVE' for r in results), results
    assert any(isinstance(r,dict) for r in results)
    await emit(label+'_manual_scheduled_insert_race', runs=1, results=[r if isinstance(r,dict) else getattr(r,'code',type(r).__name__) for r in results])
    await mark_done(Run,race)
    await Plan.filter(id=plan.id).update(enabled=False)
    await Plan.filter(id=race.id).update(enabled=False)

async def legacy_checks():
    plan = await fresh_plan(PolicyPlan, 'legacy',due=False)
    legacy = await Timed.create(tenant_id=timer.DEFAULT_TENANT_ID,name='network_policy_collection_'+plan.id,type=0,
                               status=1,tactics=CRON,end_time='2099-12-31 23:59:59',internal=True,creator_id='test-user',updator_id='test-user')
    await PolicyPlan.filter(id=plan.id).update(timed_id=legacy.id,next_run_at=None,scheduled_cron=None)
    fresh = await PolicyPlan.get(id=plan.id)
    sent=[]
    async def produce(topic,message,**kwargs):
        # A new independent connection must acquire the same row while producer waits.
        connection=await asyncpg.connect(POSTGRES_DSN, server_settings={'search_path': SCHEMA})
        try:
            await connection.execute('SET lock_timeout = \'500ms\'')
            async with connection.transaction():
                row=await connection.fetchrow('SELECT timed_id FROM network_access_policy_collection_plan WHERE id=$1 FOR UPDATE',plan.id)
                assert row['timed_id']==legacy.id
        finally:
            await connection.close()
        timed=await Timed.get(id=legacy.id)
        assert timed.enable==0 and not timed.deleted
        sent.append(message)
    original=timer.aproduce
    timer.aproduce=produce
    try:
        await asyncio.wait_for(timer.reconcile_timer(fresh,plan_model=PolicyPlan,timer_prefix='network_policy_collection_',lock_namespace='network_policy_collection'),10)
    finally:
        timer.aproduce=original
    current=await PolicyPlan.get(id=plan.id)
    assert current.timed_id is None and current.next_run_at is not None and current.scheduled_cron==CRON
    assert (await Timed.get(id=legacy.id)).deleted and len(sent)==1 and sent[0]['timer']=='remove'
    await emit('legacy_timer_retirement', messages=1, disabled_before_broker=True, no_row_lock_during_broker=True, timed_soft_deleted=True)

async def savepoint_conflict(Plan,Run,Item,runs,label,started):
    plan=await fresh_plan(Plan,label+'-savepoint-race-'+secrets.token_hex(3),due=True)
    await Item.create(tenant_id=plan.tenant_id,plan_id=plan.id,app_name='test_app',action_func='collect',resources=[{'resource_id':'resource'}])
    repository_class=runs.OrmPolicyRunRepository if label=='policy' else runs.OrmRunRepository
    original_active=repository_class.active_run
    scheduler_checked=asyncio.Event()
    manual_committed=asyncio.Event()
    scheduler_none=[]
    async def gate_active(self,*args):
        current=await original_active(self,*args)
        if asyncio.current_task().get_name()=='scheduler-savepoint' and args[0]==plan.tenant_id:
            assert current is None
            scheduler_none.append(True)
            scheduler_checked.set()
            await asyncio.wait_for(manual_committed.wait(),5)
        return current
    async def manual():
        await asyncio.wait_for(scheduler_checked.wait(),5)
        try:
            if label=='policy':
                return await runs.trigger_manual(plan.tenant_id,account_id='test-user')
            return await runs.trigger_manual(plan.tenant_id,plan.topology_id,account_id='test-user')
        finally:
            manual_committed.set()
    domain=scheduler.CollectionDomain(Plan,Run,runs.start_scheduled,runs.launch_run,'unused_','test_'+label)
    repository_class.active_run=gate_active
    before=len(started[label])
    try:
        sched=asyncio.create_task(scheduler.tick_domain(domain,now=NOW),name='scheduler-savepoint')
        await asyncio.wait_for(asyncio.gather(sched,manual()),10)
    finally:
        repository_class.active_run=original_active
    rows=await Run.filter(plan_id=plan.id)
    cursor=(await Plan.get(id=plan.id)).next_run_at
    assert scheduler_none==[True]
    assert len(rows)==1 and rows[0].trigger=='manual'
    assert cursor==scheduler.next_fire_time(CRON,NOW)
    assert len(started[label])-before==1
    await emit(label+'_nested_savepoint_unique_conflict',runs=1,trigger=rows[0].trigger,cursor_committed=True,starts=1)
    await Plan.filter(id=plan.id).update(enabled=False)

async def main():
    if not POSTGRES_DSN:
        print('SKIP: set COLLECTION_TEST_POSTGRES_DSN to run the PostgreSQL regression')
        return
    admin = await asyncpg.connect(POSTGRES_DSN)
    try:
        await admin.execute(f'CREATE SCHEMA "{SCHEMA}"')
        connection = expand_db_url(POSTGRES_DSN)
        connection['credentials'].update(
            minsize=2, maxsize=6,
            server_settings={'search_path': SCHEMA, 'statement_timeout': '8000', 'lock_timeout': '5000'},
        )
        config = {
            'connections': {'default': connection},
            'apps': {'models': {'models': ['models'], 'default_connection': 'default'}},
            'use_tz': True, 'timezone': 'UTC',
        }
        await Tortoise.init(config=config)
        db = connections.get('default')
        await emit('server', version=(await db.execute_query_dict('SHOW server_version'))[0]['server_version'])
        await migrate(db)
        started = install_external_boundaries()
        for Plan, Run, Item, runs, label in (
            (PolicyPlan, PolicyRun, PolicyItem, policy_runs, 'policy'),
            (TopologyPlan, TopologyRun, TopologyItem, topology_runs, 'topology'),
        ):
            await domain_checks(Plan, Run, Item, runs, label, started)
            await savepoint_conflict(Plan, Run, Item, runs, label, started)
        await legacy_checks()
        await emit('complete', external_action_or_agent_calls=0)
    finally:
        await Tortoise.close_connections()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        await admin.close()


if __name__ == '__main__':
    asyncio.run(main())
