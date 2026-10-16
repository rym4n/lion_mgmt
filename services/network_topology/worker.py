"""Standalone durable topology-analysis worker.

Start with ``python -m services.network_topology.worker --pipeline module:factory``.
The module deliberately has no Sanic application hook.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import signal
import socket
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from aiocache import caches
from tortoise import Tortoise

import settings
from models._network_topology_v2_base import AnalysisRunStage
from services.network_topology import alerts, leases
from services.network_topology.leases import LeaseIdentity


LOGGER = logging.getLogger(__name__)
WAKEUP_CHANNEL = "network-topology:analysis:wakeup"
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_HEARTBEAT_INTERVAL = 15.0


@dataclass(frozen=True, slots=True)
class StageResult:
    """Pure pipeline output committed by the worker under the active fence."""

    updates: Mapping[str, Any] = field(default_factory=dict)
    published: bool = False


class TopologyAnalysisPipeline(Protocol):
    async def execute_stage(
        self, run: Any, stage: AnalysisRunStage, lease: LeaseIdentity
    ) -> StageResult: ...


class PipelineFailure(RuntimeError):
    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(error_code)


class WakeupSource(Protocol):
    async def wait(self, timeout: float, stop_event: asyncio.Event) -> None: ...


class RedisWakeupSource:
    def __init__(self, cache=None):
        self.cache = cache
        self._pubsub = None
        self._pubsub_lock = asyncio.Lock()

    async def wait(self, timeout: float, stop_event: asyncio.Event) -> None:
        if stop_event.is_set():
            return
        try:
            await self._wait_for_message(timeout)
        except asyncio.TimeoutError:
            return
        except asyncio.CancelledError:
            await self._close_pubsub()
            raise
        except Exception as exc:
            await self._close_pubsub()
            LOGGER.warning("topology worker Redis wake-up unavailable: %s", type(exc).__name__)
            await _wait_for_stop(stop_event, timeout)

    async def _wait_for_message(self, timeout):
        pubsub = await self._get_pubsub()
        await asyncio.wait_for(
            pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout),
            timeout=timeout,
        )

    async def _get_pubsub(self):
        if self._pubsub is not None:
            return self._pubsub
        async with self._pubsub_lock:
            if self._pubsub is not None:
                return self._pubsub
            cache = self.cache or caches.get("redis_mgmt")
            pubsub = cache.client.pubsub()
            try:
                await pubsub.subscribe(WAKEUP_CHANNEL)
                # ``subscribe`` leaves its confirmation response queued. Consume
                # it before polling, otherwise every wait returns immediately.
                await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1
                )
            except BaseException:
                with suppress(Exception):
                    await pubsub.aclose()
                raise
            self._pubsub = pubsub
            return pubsub

    async def _close_pubsub(self) -> None:
        async with self._pubsub_lock:
            pubsub = self._pubsub
            self._pubsub = None
        if pubsub is not None:
            with suppress(Exception):
                await pubsub.aclose()

    async def aclose(self) -> None:
        await self._close_pubsub()


class PollingWakeupSource:
    async def wait(self, timeout: float, stop_event: asyncio.Event) -> None:
        await _wait_for_stop(stop_event, timeout)


class TopologyAnalysisWorker:
    def __init__(
        self,
        *,
        worker_id: str,
        pipeline: TopologyAnalysisPipeline,
        lease_repository=None,
        alert_repository=None,
        wakeup_source: WakeupSource | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    ) -> None:
        self.worker_id = worker_id
        self.pipeline = pipeline
        self.leases = lease_repository or leases.OrmLeaseRepository()
        self.alerts = alert_repository or alerts.OrmAlertRepository()
        self.wakeup = wakeup_source or PollingWakeupSource()
        self.clock = clock
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.stop_event = asyncio.Event()
        self.active_lease: LeaseIdentity | None = None

    async def run_forever(self) -> None:
        while not self.stop_event.is_set():
            worked = await self.run_once()
            if not worked:
                await self.wakeup.wait(self.poll_interval, self.stop_event)

    async def run_once(self) -> bool:
        if not settings.TOPOLOGY_V2_ANALYSIS_ENABLED:
            await self._alert_unavailable_runs()
            return False
        claimed = await leases.claim_due_run(
            self.worker_id, repository=self.leases, clock=self.clock
        )
        if claimed is None:
            return False
        await alerts.close_worker_unavailable(
            claimed.run, self.worker_id, repository=self.alerts, clock=self.clock
        )
        await self._process(claimed)
        return True

    async def shutdown(self) -> None:
        self.stop_event.set()
        try:
            close_wakeup = getattr(self.wakeup, "aclose", None)
            if close_wakeup is not None:
                await close_wakeup()
        finally:
            if self.active_lease is not None:
                await leases.release_lease(self.active_lease, repository=self.leases)

    async def _process(self, claimed: leases.ClaimedRun) -> None:
        self.active_lease = claimed.identity
        try:
            await self._run_stages(claimed.run, claimed.identity)
        except PipelineFailure as exc:
            await leases.schedule_retry(
                claimed.identity,
                exc.error_code,
                repository=self.leases,
                clock=self.clock,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("topology pipeline failed without a stable error")
            await leases.schedule_retry(
                claimed.identity,
                "PARSER_FAILED",
                repository=self.leases,
                clock=self.clock,
            )
        finally:
            await leases.release_lease(claimed.identity, repository=self.leases)
            self.active_lease = None

    async def _run_stages(self, run: Any, identity: LeaseIdentity) -> None:
        while not self.stop_event.is_set():
            stage = _as_stage(run.stage)
            diagnostic = leases.is_diagnostic_reanalysis(run)
            if (stage == AnalysisRunStage.FACTS_READY
                    and not diagnostic and not settings.TOPOLOGY_V2_PUBLISH_ENABLED):
                await leases.wait_for_publish(identity, repository=self.leases)
                return
            if stage == AnalysisRunStage.PUBLISHING:
                allowed = await leases.publication_allowed(
                    identity, repository=self.leases, clock=self.clock
                )
                if not allowed:
                    raise PipelineFailure("WORKER_LEASE_LOST")
            result = await self._execute_with_heartbeat(run, stage, identity)
            if diagnostic and stage == AnalysisRunStage.VALIDATING_GRAPH:
                # execute_stage has validated the persisted graph under the
                # active fence. Complete this run without a publication attempt.
                if not await leases.mark_succeeded(
                    identity, repository=self.leases, clock=self.clock
                ):
                    raise PipelineFailure("WORKER_LEASE_LOST")
                return
            if stage == AnalysisRunStage.PUBLISHING:
                if not result.published:
                    raise PipelineFailure("GRAPH_VALIDATION_FAILED")
                if not await leases.mark_succeeded(
                    identity, repository=self.leases, clock=self.clock
                ):
                    raise PipelineFailure("WORKER_LEASE_LOST")
                return
            following = leases.next_stage(stage)
            if not await leases.advance_stage(
                identity,
                following,
                repository=self.leases,
                clock=self.clock,
                updates=result.updates,
            ):
                raise PipelineFailure("WORKER_LEASE_LOST")
            for field, value in result.updates.items():
                setattr(run, field, value)
            run.stage = following

    async def _execute_with_heartbeat(self, run, stage, identity):
        pipeline_task = asyncio.create_task(
            self.pipeline.execute_stage(run, stage, identity)
        )
        heartbeat_task = asyncio.create_task(self._heartbeat_until_stopped(identity))
        try:
            done, _ = await asyncio.wait(
                {pipeline_task, heartbeat_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if pipeline_task in done:
                return pipeline_task.result()
            if self.stop_event.is_set():
                raise asyncio.CancelledError
            raise PipelineFailure("WORKER_LEASE_LOST")
        finally:
            for task in (pipeline_task, heartbeat_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(pipeline_task, heartbeat_task, return_exceptions=True)

    async def _heartbeat_until_stopped(self, identity):
        while not self.stop_event.is_set():
            await asyncio.sleep(self.heartbeat_interval)
            alive = await leases.heartbeat(
                identity, repository=self.leases, clock=self.clock
            )
            if not alive:
                return

    async def _alert_unavailable_runs(self):
        finder = getattr(self.leases, "due_runs_for_alert", None)
        rows = await finder(self.clock()) if finder is not None else getattr(self.leases, "runs", ())
        for run in rows:
            if leases.run_is_due(run, self.clock(), publish_enabled=False):
                await alerts.open_worker_unavailable(
                    run, repository=self.alerts, clock=self.clock
                )


async def _consume_cancelled(task):
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _wait_for_stop(stop_event, timeout):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass


def _as_stage(value):
    return value if isinstance(value, AnalysisRunStage) else AnalysisRunStage(value)


def _load_pipeline(spec: str) -> TopologyAnalysisPipeline:
    module_name, separator, factory_name = spec.partition(":")
    if not separator or not module_name or not factory_name:
        raise ValueError("pipeline must use module:factory")
    factory = getattr(importlib.import_module(module_name), factory_name)
    return factory()


def _worker_id(explicit: str | None) -> str:
    return explicit or f"{socket.gethostname()}:{uuid.uuid4()}"


def _install_signal_handlers(service: TopologyAnalysisWorker) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, lambda: asyncio.create_task(service.shutdown()))


async def _async_main(args) -> None:
    await Tortoise.init(config=settings.TORTOISE_ORM)
    caches.set_config(settings.CACHE)
    service = TopologyAnalysisWorker(
        worker_id=_worker_id(args.worker_id),
        pipeline=_load_pipeline(args.pipeline),
        wakeup_source=RedisWakeupSource(),
        poll_interval=args.poll_interval,
    )
    _install_signal_handlers(service)
    try:
        await service.run_forever()
    finally:
        await service.shutdown()
        await Tortoise.close_connections()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="topology-analysis-worker")
    parser.add_argument("--pipeline", required=True, help="module:factory")
    parser.add_argument("--worker-id")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    asyncio.run(_async_main(parser.parse_args(argv)))


if __name__ == "__main__":
    main()


__all__ = [
    "PipelineFailure", "StageResult", "TopologyAnalysisPipeline",
    "TopologyAnalysisWorker", "main",
]
