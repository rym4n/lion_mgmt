# coding=utf-8
import orjson
from sanic.log import logger

from libs.chat import chat_manager
from settings import WR_TOPIC


async def publish_command_center_update(_tenant_id: str | None, reason: str, _entity_id: str | None = None) -> None:
    """Notify connected clients that command-center data is stale.

    The payload intentionally carries no business data. Clients reload through
    normal permission-checked APIs under their own session.
    """
    payload = {"type": "command_center_updated"}
    try:
        await chat_manager.msg_pub.publish(WR_TOPIC, orjson.dumps(payload).decode())
    except Exception as exc:
        logger.warning(f"指挥中心实时事件发布失败: reason={reason}, error={exc}")
