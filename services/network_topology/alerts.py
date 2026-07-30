"""Idempotent worker-health alerts for topology synchronization runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from tortoise.exceptions import IntegrityError

from models._network_topology_v2_control import TopologySyncAlert


WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
OPEN = "OPEN"
CLOSED = "CLOSED"
_DETAILS = "Topology analysis worker has not claimed this synchronization"


class AlertRepository(Protocol):
    async def get_or_create(self, run, values): ...

    async def save(self, row, fields): ...

    async def find_open(self, run): ...


class OrmAlertRepository:
    async def get_or_create(self, run, values):
        filters = {
            "tenant_id": run.tenant_id,
            "sync_id": str(run.id),
            "alert_type": WORKER_UNAVAILABLE,
            "deleted": False,
        }
        try:
            return await TopologySyncAlert.get_or_create(defaults=values, **filters)
        except IntegrityError:
            return await TopologySyncAlert.get(**filters), False

    async def save(self, row, fields):
        await row.save(update_fields=[*fields, "utime"])

    async def find_open(self, run):
        return await TopologySyncAlert.filter(
            tenant_id=run.tenant_id,
            sync_id=str(run.id),
            alert_type=WORKER_UNAVAILABLE,
            status=OPEN,
            deleted=False,
        ).first()


async def open_worker_unavailable(
    run: Any,
    *,
    repository: AlertRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Any:
    repo = repository or OrmAlertRepository()
    now = clock()
    values = {
        "tenant_id": run.tenant_id,
        "topology_row_id": run.topology_row_id,
        "analysis_run_row_id": run.id,
        "sync_id": str(run.id),
        "alert_type": WORKER_UNAVAILABLE,
        "severity": "ERROR",
        "status": OPEN,
        "details": _DETAILS,
        "opened_at": now,
    }
    row, created = await repo.get_or_create(run, values)
    if not created and row.status != OPEN:
        _reopen(row, now)
        await repo.save(row, ("status", "opened_at", "closed_at", "closed_by"))
    return row


async def close_worker_unavailable(
    run: Any,
    worker_id: str,
    *,
    repository: AlertRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> bool:
    repo = repository or OrmAlertRepository()
    row = await repo.find_open(run)
    if row is None:
        return False
    row.status = CLOSED
    row.closed_at = clock()
    row.closed_by = worker_id
    await repo.save(row, ("status", "closed_at", "closed_by"))
    return True


def _reopen(row, now):
    row.status = OPEN
    row.opened_at = now
    row.closed_at = None
    row.closed_by = None


__all__ = [
    "AlertRepository", "CLOSED", "OPEN", "OrmAlertRepository",
    "WORKER_UNAVAILABLE", "close_worker_unavailable", "open_worker_unavailable",
]
