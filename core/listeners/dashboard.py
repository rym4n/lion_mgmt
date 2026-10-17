# coding=utf-8

import asyncio
import traceback
from asyncio.base_events import BaseEventLoop

from sanic import Sanic
from sanic.log import logger

from services.dashboard.materialized_refresh import refresh_dashboard_materialized_views

from .base import BaseListener


_DASHBOARD_REFRESH_TASK_NAME = "dashboard_materialized_view_refresh"


def _dashboard_refresh_interval_seconds() -> int:
    from settings import DASHBOARD_REFRESH_INTERVAL_SECONDS

    return DASHBOARD_REFRESH_INTERVAL_SECONDS


class DashboardMaterializedViewListener(BaseListener):
    """Refresh dashboard snapshots outside request handling."""

    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        async def refresh_loop() -> None:
            try:
                while True:
                    await asyncio.sleep(_dashboard_refresh_interval_seconds())
                    try:
                        await refresh_dashboard_materialized_views(app)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Dashboard materialized view periodic refresh failed: "
                            f"{exc} | {traceback.format_exc()}"
                        )
            except asyncio.CancelledError:
                raise

        app.add_task(refresh_loop(), name=_DASHBOARD_REFRESH_TASK_NAME)

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        try:
            await app.cancel_task(_DASHBOARD_REFRESH_TASK_NAME)
        except Exception:
            logger.warning(
                "Dashboard materialized view refresh task shutdown failed: "
                f"{traceback.format_exc()}"
            )


__all__ = ["DashboardMaterializedViewListener"]
