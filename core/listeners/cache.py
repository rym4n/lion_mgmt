# coding=utf-8

from asyncio.base_events import BaseEventLoop

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
        logger.info("初始化SSO_CONFIG")
        records = await Config.filter(module='sso').all()
        sso_config = {c.key: c.value for c in records}
        if sso_config.get('use_oauth2') == 'true':
            app.ctx.oauth2 = OAuth2(
                sso_config.get("client_id"),
                sso_config.get("client_secret"),
                sso_config.get("authorize_uri"),
                sso_config.get("token_uri"),
                refresh_token_endpoint=sso_config.get("refresh_token_uri"),
                revoke_token_endpoint=sso_config.get("revoke_token_uri"),
                base_scopes=sso_config.get("scopes").split(',')
            )

        app.add_task(chat_manager.msg_listener)
        app.add_task(chat_manager.log_listener)

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        """释放连接"""
        await app.ctx.cache_client.close()
        logger.info("Redis closed.")
