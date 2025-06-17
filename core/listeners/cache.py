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
        app.ctx.oauth_cfg = {c.key: c.value for c in records}
        if app.ctx.oauth_cfg.get('use_oauth2') == 'true':
            app.ctx.oauth2 = OAuth2(
                app.ctx.oauth_cfg.get("client_id"),
                app.ctx.oauth_cfg.get("client_secret"),
                app.ctx.oauth_cfg.get("authorize_uri"),
                app.ctx.oauth_cfg.get("token_uri"),
                refresh_token_endpoint=app.ctx.oauth_cfg.get("refresh_token_uri") or None,
                revoke_token_endpoint=app.ctx.oauth_cfg.get("revoke_token_uri") or None,
                base_scopes=app.ctx.oauth_cfg.get("scopes").split(',')
            )

        app.add_task(chat_manager.msg_listener)
        app.add_task(chat_manager.log_listener)

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        """释放连接"""
        await app.ctx.cache_client.close()
        logger.info("Redis closed.")
