# coding=utf-8
from asyncio.base_events import BaseEventLoop

from sanic import Sanic
from sanic.log import logger

from services.agent import AgentGateway, AgentLifecycleManager
from settings import RY_AGENT_SERVER

from .base import BaseListener


class AgentListener(BaseListener):
    """Agent 初始化监听"""

    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        """初始化 Gateway"""
        try:
            redis_client = getattr(app.ctx, "cache_client", None)
            if not redis_client:
                logger.warning("Redis client not found, Agent Gateway initialization skipped.")
                return

            # 获取底层的 redis-py client
            if hasattr(redis_client, "client"):
                redis_client = redis_client.client

            gateway = AgentGateway(agent_server_url=RY_AGENT_SERVER, redis_client=redis_client)

            # 初始化 Lua 脚本
            await gateway._init_scripts()

            app.ctx.agent_gateway = gateway
            app.ctx.agent_lifecycle = AgentLifecycleManager(gateway)

            logger.info("Agent Gateway initialized successfully")

        except Exception as e:
            logger.error(f"Agent Gateway initialization failed: {e}")

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        """清理资源"""
        return
