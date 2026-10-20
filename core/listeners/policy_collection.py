"""Sanic lifecycle for the network policy collection run sweep."""

import asyncio
from contextlib import suppress

from sanic.log import logger

from .base import BaseListener


class PolicyCollectionListener(BaseListener):
    async def after_server_start(self, app, loop) -> None:
        async def sweep():
            from services.network_access_control.collection import runs

            while True:
                await asyncio.sleep(60)
                try:
                    await runs.sweep_runs()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("策略采集 run sweep 异常")

        task = getattr(app.ctx, "policy_collection_sweep_task", None)
        if task is None or task.done():
            app.ctx.policy_collection_sweep_task = asyncio.create_task(sweep(), name="policy_collection_run_sweep")

    async def before_server_stop(self, app, loop) -> None:
        task = getattr(app.ctx, "policy_collection_sweep_task", None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
