"""Tracked in-process collection tasks with a durable heartbeat."""
from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import suppress
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_STALE_SECONDS = 120
_ACTIVE_TASKS: set[asyncio.Task] = set()


async def _write_heartbeat(run, run_model):
    try:
        await run_model.filter(id=run.id, tenant_id=run.tenant_id, deleted=False).update(
            heartbeat_at=datetime.now(timezone.utc))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Collection heartbeat failed for run %s", run.id)


async def _beat(run, run_model, interval):
    while True:
        await asyncio.sleep(interval)
        await _write_heartbeat(run, run_model)


async def _tracked(coro, run, run_model, interval):
    # Persist the initial beat before work can finish, even without yielding.
    await _write_heartbeat(run, run_model)
    beat = asyncio.create_task(_beat(run, run_model, interval))
    try:
        return await coro
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat


def _done(task: asyncio.Task, coro) -> None:
    _ACTIVE_TASKS.discard(task)
    # Cancellation can happen before _tracked starts or during its initial DB
    # write, before it has awaited the owned coroutine.
    if inspect.iscoroutine(coro):
        coro.close()
    if not task.cancelled() and task.exception() is not None:
        logger.error("Background collection task failed")


def start_tracked(coro, *, run, run_model, interval: float | None = None) -> asyncio.Task:
    task = asyncio.create_task(_tracked(
        coro, run, run_model, HEARTBEAT_INTERVAL_SECONDS if interval is None else interval))
    _ACTIVE_TASKS.add(task)
    task.add_done_callback(lambda finished: _done(finished, coro))
    return task


def heartbeat_stale(run, now: datetime | None = None) -> bool:
    reference = getattr(run, "heartbeat_at", None) or getattr(run, "started_at", None)
    if reference is None:
        return False
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - reference).total_seconds() > HEARTBEAT_STALE_SECONDS
