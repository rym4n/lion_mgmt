# coding=utf-8

from asyncio.base_events import BaseEventLoop
from functools import partial

from aiocache import caches
from httpx_oauth.oauth2 import OAuth2
from sanic import Sanic
from sanic.log import logger

from libs.chat import chat_manager
from models import Config
from settings import CACHE
from .base import BaseListener


class CacheListener(BaseListener):
    """缓存服务监听"""

    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        """app绑定内存/Redis"""
        caches.set_config(CACHE)
        app.ctx.cache_client = caches.get('redis_mgmt')
        logger.info("使用Redis作为缓存")

        records = await Config.filter(module='sso').values('key', 'value')
        app.ctx.oauth_cfg = {c['key']: c['value'] for c in records}

        if app.ctx.oauth_cfg.get('use_oauth2') == 'true':
            logger.info("启用OAuth2认证")
            app.ctx.oauth2 = OAuth2(
                app.ctx.oauth_cfg.get("client_id"),
                app.ctx.oauth_cfg.get("client_secret"),
                app.ctx.oauth_cfg.get("authorize_uri"),
                app.ctx.oauth_cfg.get("token_uri"),
                base_scopes=app.ctx.oauth_cfg["scopes"].split(',') if app.ctx.oauth_cfg.get("scopes") else None
            )

        app.add_task(chat_manager.msg_listener)
        app.add_task(partial(chat_manager.log_listener, app))

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        """释放连接"""
        await app.ctx.cache_client.close()
        logger.info("Redis closed.")
