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

    async def before_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        """初始化缓存"""
        try:
            caches.set_config(CACHE)
            app.ctx.cache_client = caches.get('redis_mgmt')
            logger.info("使用Redis作为缓存")
        except Exception as e:
            logger.error(f"缓存初始化失败: {e}")
            raise

    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        """app绑定内存/Redis"""
        records = await Config.filter(module='sso').values('key', 'value')
        app.ctx.oauth_cfg = {c['key']: c['value'] for c in records}

        if app.ctx.oauth_cfg.get('use_oauth2') == 'true':
            logger.info("启用OAuth2认证")
            client_id = app.ctx.oauth_cfg.get("client_id")
            client_secret = app.ctx.oauth_cfg.get("client_secret")
            authorize_uri = app.ctx.oauth_cfg.get("authorize_uri")
            token_uri = app.ctx.oauth_cfg.get("token_uri")

            if not all([client_id, client_secret, authorize_uri, token_uri]):
                logger.warning("OAuth2 配置缺失，请检查数据库中 sso 模块相关配置")
                return

            scopes = app.ctx.oauth_cfg.get("scopes")
            base_scopes = scopes.split(',') if scopes else None

            app.ctx.oauth2 = OAuth2(
                client_id,
                client_secret,
                authorize_uri,
                token_uri,
                base_scopes=base_scopes
            )

        app.add_task(chat_manager.msg_listener)
        app.add_task(chat_manager.log_listener)

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        """释放连接"""
        cache_client = getattr(app.ctx, 'cache_client', None)
        if cache_client:
            try:
                await cache_client.close()
                logger.info("Redis closed.")
            except Exception as e:
                logger.warning(f"关闭 Redis 连接时出错: {e}")

    async def _safe_task(self, coro):
        """包装任务以捕获异常"""
        try:
            await coro()
        except Exception as e:
            logger.exception(f"后台任务执行异常: {e}")
