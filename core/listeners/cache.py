# coding=utf-8
import traceback
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
        """初始化缓存配置"""
        try:
            caches.set_config(CACHE)
            app.ctx.cache_client = caches.get("redis_mgmt")
            logger.info("使用Redis作为缓存")
        except Exception as e:
            logger.error(f"Cache initialization failed: {e} | {traceback.format_exc()}")

    async def after_server_start(self, app: Sanic, loop: BaseEventLoop) -> None:
        """app绑定内存/Redis"""
        try:
            records = await Config.filter(module="sso").values("key", "value")
            app.ctx.oauth_cfg = {c["key"]: c["value"] for c in records}

            if app.ctx.oauth_cfg.get("use_oauth2") == "true":
                logger.info("启用OAuth2认证")
                client_id = app.ctx.oauth_cfg.get("client_id")
                client_secret = app.ctx.oauth_cfg.get("client_secret")
                authorize_uri = app.ctx.oauth_cfg.get("authorize_uri")
                token_uri = app.ctx.oauth_cfg.get("token_uri")

                if not all([client_id, client_secret, authorize_uri, token_uri]):
                    logger.warning("OAuth2 配置缺失，请检查数据库中 sso 模块相关配置")
                    return

                scopes = app.ctx.oauth_cfg.get("scopes")
                base_scopes = scopes.split(",") if scopes else None

                app.ctx.oauth2 = OAuth2(
                    client_id,
                    client_secret,
                    authorize_uri,
                    token_uri,
                    base_scopes=base_scopes,
                )
        except Exception as e:
            logger.error(f"OAuth initialization failed: {e} | {traceback.format_exc()}")

        app.add_task(chat_manager.msg_listener, name="chat_msg_listener")
        app.add_task(chat_manager.log_listener, name="run_log_listener")

    async def before_server_stop(self, app: Sanic, loop: BaseEventLoop) -> None:
        """关闭后台任务，释放连接"""
        try:
            await app.cancel_task("chat_msg_listener")
            await app.cancel_task("run_log_listener")
        except Exception as e:
            logger.warning(f"释放资源异常: {e} | {traceback.format_exc()}")
        finally:
            cache_client = getattr(app.ctx, "cache_client", None)
            if cache_client:
                await cache_client.close()
                logger.info("Redis closed.")
