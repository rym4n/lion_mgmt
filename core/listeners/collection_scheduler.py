"""Run direct collection schedules independently of maintenance playbooks."""
import asyncio
from contextlib import suppress

from sanic.log import logger

from .base import BaseListener


class CollectionSchedulerListener(BaseListener):
    async def after_server_start(self, app, loop) -> None:
        task = getattr(app.ctx, "collection_scheduler_task", None)
        if task is not None and not task.done():
            return

        async def run():
            from services.action_collection.scheduler import tick

            while True:
                try:
                    await tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Collection scheduler round failed")
                await asyncio.sleep(5)

        app.ctx.collection_scheduler_task = asyncio.create_task(run(), name="collection_scheduler")

    async def before_server_stop(self, app, loop) -> None:
        task = getattr(app.ctx, "collection_scheduler_task", None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
