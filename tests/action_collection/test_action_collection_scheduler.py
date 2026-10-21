"""Exercise persisted schedules/runs with the real ORM in an isolated process."""
import os
from pathlib import Path
import subprocess
import sys


def test_cron_requires_both_day_of_month_and_weekday_like_legacy_scheduler():
    from datetime import datetime, timezone
    from services.action_collection.scheduler import next_fire_time

    # 2027-02-01 is the next first of a month that is also a Monday.
    assert next_fire_time("0 0 3 1 * 1", datetime(2026, 10, 22, tzinfo=timezone.utc)) == datetime(
        2027, 1, 31, 19, tzinfo=timezone.utc)


def test_direct_collection_scheduler_persistence_and_recovery():
    script = r'''
import asyncio
from datetime import datetime, timedelta, timezone
from tortoise import Tortoise, connections
from models._network_policy_collection import NetworkAccessPolicyCollectionPlan as PolicyPlan, NetworkAccessPolicyCollectionRun as PolicyRun
from models.network_topology_v2 import TopologyCollectionPlan as TopologyPlan, TopologyCollectionRun as TopologyRun
from services.action_collection import scheduler
from services.network_access_control.collection import runs as policy_runs
from services.network_topology.collection import runs as topology_runs

async def main():
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
    try:
        db = connections.get("default")
        generator = db.schema_generator(db)
        for model in (PolicyPlan, PolicyRun, TopologyPlan, TopologyRun):
            await db.execute_script(generator._get_table_sql(model, True)["table_creation_string"])
        now = datetime.now(timezone.utc).replace(microsecond=0)
        assert scheduler.next_fire_time("0 0 3 * * *", datetime(2026,10,22,0,0,tzinfo=timezone.utc)) == datetime(2026,10,22,19,0,tzinfo=timezone.utc)
        for Plan, Run, runs in ((PolicyPlan, PolicyRun, policy_runs), (TopologyPlan, TopologyRun, topology_runs)):
            values = {"topology_id": "top"} if Plan is TopologyPlan else {}
            plan = await Plan.create(tenant_id="tenant", enabled=True, schedule_cron="0 * * * * *",
                                     scheduled_cron="0 * * * * *", next_run_at=now-timedelta(minutes=5), **values)
            launched = []
            async def launch(plan_id, run_id):
                # External action executor boundary: claim the queued run.
                row = await Run.get(id=run_id)
                assert row.plan_id == plan_id and row.tenant_id == "tenant"
                assert (await Plan.get(id=plan_id)).next_run_at > now
                launched.append(run_id)
                await Run.filter(id=run_id, state="PENDING").update(state="COLLECTING")
            domain = scheduler.CollectionDomain(Plan, Run, runs.start_scheduled, launch, "unused_", "unused")
            await scheduler.tick_domain(domain, now=now)
            assert await Run.filter(plan_id=plan.id).count() == 1
            assert len(launched) == 1
            await scheduler.tick_domain(domain, now=now)
            assert len(launched) == 1
            # A new due slot while a previous run is active is consumed, not duplicated.
            await Plan.filter(id=plan.id).update(next_run_at=now-timedelta(seconds=1))
            await scheduler.tick_domain(domain, now=now)
            assert await Run.filter(plan_id=plan.id).count() == 1
            assert (await Plan.get(id=plan.id)).next_run_at > now
            # Restart between durable creation and background launch recovers the PENDING run.
            await Run.filter(plan_id=plan.id).update(state="SUCCEEDED")
            pending = await runs.start_scheduled(plan.id, launch=False)
            await Plan.filter(id=plan.id).update(enabled=False)
            await scheduler.tick_domain(domain, now=now)
            assert launched[-1] == pending["collection_run_id"]
            assert len(launched) == 2
            assert await Run.filter(plan_id=plan.id).count() == 2
            # Disabled plans do not create additional scheduled runs.
            await Run.filter(plan_id=plan.id).update(state="SUCCEEDED")
            await Plan.filter(id=plan.id).update(next_run_at=now-timedelta(days=1))
            await scheduler.tick_domain(domain, now=now)
            assert await Run.filter(plan_id=plan.id).count() == 2
            await Plan.filter(id=plan.id).update(deleted=True)
    finally:
        await Tortoise.close_connections()
asyncio.run(main())
'''
    completed = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                               env=dict(os.environ, PYTHONPATH="."), text=True, capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_scheduler_listener_is_registered_and_recovers_after_round_failure():
    script = r'''
import asyncio
from types import SimpleNamespace
from core.listeners import LISTENER_TUPLE
from core.listeners.collection_scheduler import CollectionSchedulerListener
from services.action_collection import scheduler

async def main():
    assert CollectionSchedulerListener in LISTENER_TUPLE
    listener = CollectionSchedulerListener()
    app = SimpleNamespace(ctx=SimpleNamespace())
    calls = 0
    resumed = asyncio.Event()
    real_sleep = asyncio.sleep
    async def tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        resumed.set()
    async def sleep(seconds):
        assert seconds == 5
        if calls >= 2:
            await asyncio.Event().wait()
        await real_sleep(0)
    scheduler.tick = tick
    asyncio.sleep = sleep
    try:
        await listener.after_server_start(app, None)
        original = app.ctx.collection_scheduler_task
        await listener.after_server_start(app, None)
        assert app.ctx.collection_scheduler_task is original
        await asyncio.wait_for(resumed.wait(), 2)
        assert calls == 2 and not original.done()
    finally:
        await listener.before_server_stop(app, None)
    assert original.cancelled()
asyncio.run(main())
'''
    completed = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                               env=dict(os.environ, PYTHONPATH="."), text=True, capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr
