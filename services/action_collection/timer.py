"""Direct schedule reconciliation and retryable retirement of legacy flow timers."""
from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256
from datetime import datetime, timezone

import asyncpg
from tortoise import connections
from tortoise.transactions import in_transaction

from core.maintenance import DEFAULT_TENANT_ID
from libs.kafka import aproduce
from models import Timed
from settings import TASK_TOPIC

TIMER_END = "2099-12-31 23:59:59"
_POOL_ONLY_OPTIONS = (
    "min_size", "max_size", "max_queries", "max_inactive_connection_lifetime",
    "connect", "setup", "init", "reset",
)


def lock_key(namespace: str, tenant_id, plan_id) -> int:
    return int.from_bytes(
        sha256(f"{namespace}:{tenant_id}:{plan_id}".encode()).digest()[:8],
        "big", signed=True,
    )


@asynccontextmanager
async def advisory_lock(namespace: str, tenant_id, plan_id):
    # Waiters need their own session while the lock owner commits via the ORM pool.
    client = connections.get("default")
    options = client.extra.copy()
    for option in _POOL_ONLY_OPTIONS:
        options.pop(option, None)
    connection = await asyncpg.connect(
        host=client.host, port=client.port, user=client.user,
        password=client.password, database=client.database,
        server_settings=client.server_settings, **options,
    )
    try:
        await connection.execute("SELECT pg_advisory_lock($1)", lock_key(namespace, tenant_id, plan_id))
        yield
    finally:
        await connection.close()


def locked_plan(plan_model):
    @asynccontextmanager
    async def locked(plan):
        async with in_transaction() as db:
            current = await plan_model.filter(
                id=plan.id, tenant_id=plan.tenant_id, deleted=False,
            ).using_db(db).select_for_update().first()
            yield current

    return locked


def update_schedule_cursor(plan, now):
    from services.action_collection.scheduler import next_fire_time

    if not plan.enabled:
        plan.next_run_at = None
        plan.scheduled_cron = None
    elif (getattr(plan, "next_run_at", None) is None
          or getattr(plan, "scheduled_cron", None) != plan.schedule_cron):
        plan.next_run_at = next_fire_time(plan.schedule_cron, now)
        plan.scheduled_cron = plan.schedule_cron


async def prepare_timer_locked(plan, *, timer_prefix, timed_model=None, now=None):
    """Persist the direct cursor and disable any old job before removing it."""
    timed_model = timed_model or Timed
    update_schedule_cursor(plan, now or datetime.now(timezone.utc))
    timed = await timed_model.filter(
        name=f"{timer_prefix}{plan.id}", tenant_id=DEFAULT_TENANT_ID,
        deleted=False, internal=True,
    ).first()
    if timed is not None:
        timed.enable = 0
        await timed.save(update_fields=["enable"])
        # Keep the ID until Kafka acknowledges removal; subsequent sweeps retry.
        plan.timed_id = timed.id
    else:
        plan.timed_id = None
    await plan.save(update_fields=["next_run_at", "scheduled_cron", "timed_id"])
    if timed is None:
        return None
    return timed, dict(
        run_type="flow", run_args=timed.playbook_input or {}, timer="remove",
        timer_rule={}, timer_id=timed.id, tenant_id=DEFAULT_TENANT_ID,
    )


async def reconcile_timer_locked(
    plan, *, timer_prefix, locked_plan, timed_model=None, produce=None, now=None,
):
    produce = produce or aproduce
    async with locked_plan(plan) as current:
        prepared = None if current is None else await prepare_timer_locked(
            current, timer_prefix=timer_prefix, timed_model=timed_model, now=now,
        )
    if prepared is None:
        return
    timed, message = prepared
    # Never hold ORM row locks while waiting for the broker.
    await produce(TASK_TOPIC, message, raise_on_error=True)
    async with locked_plan(plan) as current:
        if current is None:
            return
        timed.deleted = True
        await timed.save(update_fields=["deleted"])
        current.timed_id = None
        await current.save(update_fields=["timed_id"])


async def reconcile_timer(plan, *, plan_model, timer_prefix, lock_namespace) -> None:
    async with advisory_lock(lock_namespace, plan.tenant_id, plan.id):
        await reconcile_timer_locked(
            plan, timer_prefix=timer_prefix, locked_plan=locked_plan(plan_model),
        )


async def disable_and_remove_timer(plan, *, plan_model, timer_prefix, lock_namespace) -> bool:
    locked = locked_plan(plan_model)
    async with advisory_lock(lock_namespace, plan.tenant_id, plan.id):
        async with locked(plan) as current:
            if current is None:
                return False
            current.enabled = False
            await current.save(update_fields=["enabled"])
        await reconcile_timer_locked(plan, timer_prefix=timer_prefix, locked_plan=locked)
    return True
