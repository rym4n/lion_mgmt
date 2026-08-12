import asyncio
import traceback
from asyncio.base_events import BaseEventLoop

from sanic import Sanic
from sanic.log import logger

from .base import BaseListener


class EventIngestionTemplateRepairListener(BaseListener):
    """Upgrade legacy encrypted event-ingestion templates after startup."""

    task_name = "event_ingestion_template_repair"

    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        async def repair_templates() -> None:
            try:
                from services.event_ingestion.template_repair import (
                    repair_qax_skyeye_templates,
                )

                repaired = await repair_qax_skyeye_templates()
                if repaired:
                    logger.info(f"已修复奇安信天眼解析模板: count={repaired}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - repair failure must not block service startup
                logger.warning(
                    f"奇安信天眼解析模板修复失败: {exc} | {traceback.format_exc()}"
                )

        app.add_task(repair_templates(), name=self.task_name)

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        try:
            await app.cancel_task(self.task_name)
        except Exception as exc:  # noqa: BLE001 - shutdown must continue if the task already ended
            logger.debug(f"取消奇安信天眼模板修复任务时已结束: {exc}")
