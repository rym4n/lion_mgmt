"""Internal Timed reconciliation shared by action collection domains."""
from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256

import asyncpg
from tortoise import connections
from tortoise.transactions import in_transaction

from core.maintenance import DEFAULT_TENANT_ID
from libs.kafka import aproduce
from models import Playbook, PlaybookVersion, Timed
from services.action_collection.errors import CollectionError
from settings import TASK_TOPIC

TIMER_END = "2099-12-31 23:59:59"
_POOL_ONLY_OPTIONS = (
    "min_size", "max_size", "max_queries", "max_inactive_connection_lifetime",
    "connect", "setup", "init", "reset",
)


async def builtin_playbook(name: str):
    playbook = await Playbook.filter(
        name=name, tenant_id=DEFAULT_TENANT_ID, deleted=False, internal=True,
    ).first()
    if playbook is None:
        return None, None
    version = await PlaybookVersion.filter(
        playbook_id=playbook.id, tenant_id=DEFAULT_TENANT_ID,
        deleted=False, status=1,
    ).order_by("-utime").first()
    return playbook, version


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


async def prepare_timer_locked(plan, *, timer_prefix, playbook_lookup, timed_model=None):
    timed_model = timed_model or Timed
    playbook = version = None
    if plan.enabled:
        playbook, version = await playbook_lookup()
        if playbook is None or version is None:
            raise CollectionError("COLLECTION_PLAYBOOK_UNAVAILABLE", 409)
    name = f"{timer_prefix}{plan.id}"
    timed = await timed_model.filter(
        name=name, tenant_id=DEFAULT_TENANT_ID, deleted=False, internal=True,
    ).first()
    if timed is None and not plan.enabled:
        return None
    if timed is None:
        timed = await timed_model.create(
            name=name, tenant_id=DEFAULT_TENANT_ID, type=0,
            playbook=playbook, playbook_version=version.version,
            playbook_input={}, status=1, tactics=plan.schedule_cron,
            end_time=TIMER_END, enable=0, creator_id=plan.updater,
            updator_id=plan.updater, internal=True,
        )
    inputs = (
        {
            "flow_id": version.id,
            "flow_input": {"plan_id": plan.id, "collection_run_id": ""},
            "executor": plan.updater,
        }
        if plan.enabled else timed.playbook_input
    )
    message = dict(
        run_type="flow", run_args=inputs,
        timer="cron" if plan.enabled else "remove",
        timer_rule=dict(end_date=TIMER_END, crontab=plan.schedule_cron) if plan.enabled else {},
        timer_id=timed.id, tenant_id=DEFAULT_TENANT_ID,
    )
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
    await plan.save(update_fields=["timed_id"])
    return timed, message, bool(plan.enabled), plan.schedule_cron


async def reconcile_timer_locked(
    plan, *, timer_prefix, locked_plan, playbook_lookup, timed_model=None, produce=None,
):
    produce = produce or aproduce
    prepared = None
    async with locked_plan(plan) as current:
        if current is not None:
            prepared = await prepare_timer_locked(
                current, timer_prefix=timer_prefix,
                playbook_lookup=playbook_lookup, timed_model=timed_model,
            )
    if prepared is None:
        return
    timed, message, enabled, cron = prepared
    # Persist retry state before publishing; a newer schedule must win on re-read.
    await produce(TASK_TOPIC, message, raise_on_error=True)
    async with locked_plan(plan) as current:
        if current is None:
            return
        if bool(current.enabled) != enabled or current.schedule_cron != cron:
            return
        timed.enable = int(enabled)
        timed.tactics = cron
        await timed.save()
        current.timed_id = timed.id
        await current.save(update_fields=["timed_id"])


async def reconcile_timer(
    plan, *, plan_model, timer_prefix, playbook_name, lock_namespace,
) -> None:
    async with advisory_lock(lock_namespace, plan.tenant_id, plan.id):
        await reconcile_timer_locked(
            plan, timer_prefix=timer_prefix,
            locked_plan=locked_plan(plan_model),
            playbook_lookup=lambda: builtin_playbook(playbook_name),
        )


async def disable_and_remove_timer(
    plan, *, plan_model, timer_prefix, playbook_name, lock_namespace,
) -> bool:
    locked = locked_plan(plan_model)
    async with advisory_lock(lock_namespace, plan.tenant_id, plan.id):
        async with locked(plan) as current:
            if current is None:
                return False
            current.enabled = False
            await current.save(update_fields=["enabled"])
            prepared = await prepare_timer_locked(
                current, timer_prefix=timer_prefix,
                playbook_lookup=lambda: builtin_playbook(playbook_name),
            )
        if prepared is not None:
            await aproduce(TASK_TOPIC, prepared[1], raise_on_error=True)
    return True
