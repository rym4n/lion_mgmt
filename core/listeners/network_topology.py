# coding=utf-8
import asyncio
from asyncio.base_events import BaseEventLoop
import os
import traceback

from sanic import Sanic
from sanic.log import logger

from .base import BaseListener


_TASK_NAME = "network_topology_refresh_sweeper"
_SWEEP_INTERVAL_SECONDS = max(
    5,
    int(os.environ.get("RY_NETWORK_TOPOLOGY_SWEEP_INTERVAL_SECONDS") or 30),
)
_SWEEP_BATCH_SIZE = max(
    1,
    min(16, int(os.environ.get("RY_NETWORK_TOPOLOGY_SWEEP_BATCH_SIZE") or 4)),
)


class NetworkTopologyRefreshListener(BaseListener):
    """Runs configuration-driven topology refreshes in a bounded background loop."""

    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        async def _refresh_loop() -> None:
            from services.network_topology.refresh import (
                migrate_legacy_topology_secrets,
                sweep_due_topologies,
            )

            try:
                migrated = await migrate_legacy_topology_secrets()
                if any(migrated.values()):
                    logger.info("网络拓扑历史敏感数据加密完成: %s", migrated)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "网络拓扑历史敏感数据加密失败，将在下次启动重试: %s | %s",
                    exc,
                    traceback.format_exc(),
                )

            while True:
                try:
                    claimed = await sweep_due_topologies(limit=_SWEEP_BATCH_SIZE)
                    if claimed:
                        logger.info("网络拓扑后台刷新完成: claimed=%s", claimed)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "网络拓扑后台刷新异常: %s | %s",
                        exc,
                        traceback.format_exc(),
                    )

                try:
                    await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise

        app.add_task(_refresh_loop(), name=_TASK_NAME)

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        try:
            await app.cancel_task(_TASK_NAME)
        except Exception as exc:
            logger.debug("取消网络拓扑后台刷新任务时忽略异常: %s", exc)
