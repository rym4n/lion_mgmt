# coding=utf-8
import traceback

import orjson
from sanic import Blueprint, Request, Websocket
from sanic.log import logger
from websockets import ConnectionClosed

from core.error_code import ErrorCode
from libs.chat import chat_manager
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission

ai_bp = Blueprint('ai', url_prefix='/ai')
__all__ = ['ai_bp']


@ai_bp.websocket('/question')
@requires_permission('warroom:r')
async def ai_ws(request: Request, ws: Websocket):
    """WebSocket连接处理(专门处理ai类型消息)"""
    account = None
    try:
        # 先获取认证信息
        # auth_data = await asyncio.wait_for(ws.recv(), timeout=15)
        # auth_json = orjson.loads(auth_data)
        # payload = jwt.decode(auth_json.get('auth', ''), SECRET, SESSION_ENCODING_ALGORITHM)
        # validate_token(payload, request)

        # account = await request.app.ctx.cache_client.get(f"account_{payload['sub']}")
        account = await request.ctx.session.get('account_info')
        if not account:
            logger.warning("redis里已经没有account信息了")
            raise CustomError(ErrorCode.SessionInvalid)
        # 建立连接
        await chat_manager.connect(account, ws, "ai")
        # 持续接收和处理消息f
        async for msg in ws:
            try:
                message = orjson.loads(msg)
                await chat_manager.handle_ai_message(account, ws, message)
            except Exception as e1:
                logger.error(f"{e1} | {traceback.format_exc()}")
    except ConnectionClosed as e2:
        logger.error(f"{e2} | {traceback.format_exc()}")
    except Exception as e:
        logger.error(f"{e} | {traceback.format_exc()}")
        await ws.send(orjson.dumps({"type": "error", "message": str(e)}).decode())
    finally:
        if account:
            await chat_manager.disconnect(account.id)
        await ws.close()
