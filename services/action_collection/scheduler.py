"""Durable cron cursors for directly orchestrated collection runs."""
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Callable
from zoneinfo import ZoneInfo

from croniter import croniter
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from services.action_collection import timer
from services.action_collection.errors import CollectionError

logger = logging.getLogger(__name__)
SCHEDULE_TIMEZONE = ZoneInfo("Asia/Shanghai")


def next_fire_time(cron: str, now: datetime) -> datetime:
    # APScheduler applied day-of-month and weekday together for existing plans.
    return croniter(
        cron, now.astimezone(SCHEDULE_TIMEZONE), second_at_beginning=True, day_or=False,
    ).get_next(datetime).astimezone(timezone.utc)


@dataclass(frozen=True)
class CollectionDomain:
    plan_model: type
    run_model: type
    start_scheduled: Callable
    launch_run: Callable
    timer_prefix: str
    lock_namespace: str


async def _claim_due(domain, plan, now):
    async with in_transaction() as db:
        current = await domain.plan_model.filter(
            id=plan.id, tenant_id=plan.tenant_id, deleted=False,
        ).using_db(db).select_for_update().first()
        if current is None or not current.enabled or current.timed_id:
            return
        # A concurrent plan edit must be reconciled before it can become due.
        if current.scheduled_cron != current.schedule_cron or current.next_run_at is None:
            timer.update_schedule_cursor(current, now)
            await current.save(using_db=db, update_fields=["next_run_at", "scheduled_cron"])
            return
        if current.next_run_at > now:
            return
        try:
            # A nested savepoint keeps a uniqueness conflict from aborting the
            # plan transaction. Cursor advancement and run creation commit together.
            async with in_transaction():
                await domain.start_scheduled(str(current.id), launch=False)
        except CollectionError as exc:
            if exc.code not in {"COLLECTION_RUN_ACTIVE", "COLLECTION_PLAN_DISABLED"}:
                raise
        # Coalesce missed slots after downtime and skip overlapping occurrences.
        current.next_run_at = next_fire_time(current.schedule_cron, now)
        await current.save(using_db=db, update_fields=["next_run_at"])


async def tick_domain(domain: CollectionDomain, *, now=None):
    now = now or datetime.now(timezone.utc)
    plans = await domain.plan_model.filter(
        Q(enabled=True) | Q(timed_id__isnull=False), deleted=False,
    )
    for plan in plans:
        try:
            if (plan.timed_id or plan.next_run_at is None
                    or plan.scheduled_cron != plan.schedule_cron):
                await timer.reconcile_timer(
                    plan, plan_model=domain.plan_model, timer_prefix=domain.timer_prefix,
                    lock_namespace=domain.lock_namespace,
                )
            await _claim_due(domain, plan, now)
        except Exception:
            logger.exception("Collection schedule reconciliation failed for plan %s", plan.id)
    # Durable PENDING runs also cover a restart between committing a due slot
    # (or a manual request) and creating the in-process background task.
    pending = await domain.run_model.filter(
        deleted=False, state="PENDING", flow_run_id__isnull=True, heartbeat_at__isnull=True,
    )
    for run in pending:
        try:
            await domain.launch_run(str(run.plan_id), str(run.id))
        except CollectionError as exc:
            logger.warning("Collection launch rejected for run %s: %s", run.id, exc.code)
        except Exception:
            logger.exception("Collection launch failed for run %s", run.id)


async def tick():
    from models.network_access_control import NetworkAccessPolicyCollectionPlan, NetworkAccessPolicyCollectionRun
    from models.network_topology_v2 import TopologyCollectionPlan, TopologyCollectionRun
    from services.network_access_control.collection import plans as policy_plans, runs as policy_runs
    from services.network_topology.collection import plans as topology_plans, runs as topology_runs

    for domain in (
        CollectionDomain(TopologyCollectionPlan, TopologyCollectionRun, topology_runs.start_scheduled,
                         topology_runs.launch_run, topology_plans.TIMER_PREFIX, topology_plans.LOCK_NAMESPACE),
        CollectionDomain(NetworkAccessPolicyCollectionPlan, NetworkAccessPolicyCollectionRun,
                         policy_runs.start_scheduled, policy_runs.launch_run,
                         policy_plans.TIMER_PREFIX, policy_plans.LOCK_NAMESPACE),
    ):
        try:
            await tick_domain(domain)
        except Exception:
            logger.exception("Collection scheduler round failed for %s", domain.plan_model.__name__)
