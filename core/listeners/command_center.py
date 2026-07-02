# coding=utf-8
import asyncio
from asyncio.base_events import BaseEventLoop
import traceback

from sanic import Sanic
from sanic.log import logger

from .base import BaseListener


class CommandCenterListener(BaseListener):
    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        async def _recover_approval_resumes() -> None:
            try:
                from services.command_center.approval_service import recover_pending_approval_resumes

                recovered = await recover_pending_approval_resumes(operator_id="system-startup-recovery")
                if recovered:
                    logger.info(f"指挥中心审批续跑恢复完成: count={len(recovered)}")
            except Exception as exc:
                logger.warning(f"指挥中心审批续跑恢复失败: {exc} | {traceback.format_exc()}")

        async def _sweep_stale_dispatch_tasks() -> None:
            from settings import COMMAND_TASK_SWEEP_INTERVAL_SECONDS
            from services.command_center.manager import sweep_stale_dispatch_tasks

            while True:
                try:
                    await asyncio.sleep(COMMAND_TASK_SWEEP_INTERVAL_SECONDS)
                    swept = await sweep_stale_dispatch_tasks(operator_id="system-dispatch-timeout")
                    if swept:
                        logger.info(f"指挥中心派单超时清扫完成: count={swept}")
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning(f"指挥中心派单超时清扫异常: {exc} | {traceback.format_exc()}")

        app.add_task(_recover_approval_resumes(), name="command_center_approval_resume_recovery")
        app.add_task(_sweep_stale_dispatch_tasks(), name="command_center_dispatch_timeout_sweeper")

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        for task_name in ("command_center_approval_resume_recovery", "command_center_dispatch_timeout_sweeper"):
            try:
                await app.cancel_task(task_name)
            except Exception:
                pass
