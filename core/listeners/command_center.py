# coding=utf-8
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

        app.add_task(_recover_approval_resumes(), name="command_center_approval_resume_recovery")

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        try:
            await app.cancel_task("command_center_approval_resume_recovery")
        except Exception:
            pass
