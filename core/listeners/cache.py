# coding=utf-8

from asyncio.base_events import BaseEventLoop

from aiocache import caches
from sanic import Sanic
from sanic.log import logger

from settings import CACHE
from .base import BaseListener


class CacheListener(BaseListener):
    """缓存服务监听"""


    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        """app绑定内存/Redis"""
        caches.set_config(CACHE)
        if CACHE['redis']['endpoint']:
            app.ctx.cache_client = caches.get('redis')
            logger.info("使用Redis作为缓存")
        else:
            app.ctx.cache_client = caches.get('default')
            logger.info("使用内存作为缓存")

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        """释放连接"""
        await app.ctx.cache_client.close()
        logger.info("Redis closed.")
