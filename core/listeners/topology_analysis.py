"""Sanic lifecycle for the durable in-process topology analysis worker."""

import asyncio
from contextlib import suppress

from sanic.log import logger

from .base import BaseListener


class TopologyAnalysisListener(BaseListener):
    async def after_server_start(self, app, loop) -> None:
        task = getattr(app.ctx, "topology_analysis_task", None)
        if task is not None and not task.done():
            return
        from services.network_topology.analysis_pipeline import build_default_pipeline
        from services.network_topology.worker import RedisWakeupSource, TopologyAnalysisWorker, _worker_id

        worker = TopologyAnalysisWorker(
            worker_id=_worker_id(None), pipeline=build_default_pipeline(),
            wakeup_source=RedisWakeupSource(),
        )
        app.ctx.topology_analysis_worker = worker

        async def run():
            try:
                await worker.run_forever()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("拓扑分析 worker 异常退出")

        app.ctx.topology_analysis_task = asyncio.create_task(run(), name="topology_analysis_worker")

    async def before_server_stop(self, app, loop) -> None:
        worker = getattr(app.ctx, "topology_analysis_worker", None)
        task = getattr(app.ctx, "topology_analysis_task", None)
        try:
            if worker is not None:
                await worker.shutdown()
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
