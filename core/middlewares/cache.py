# coding=utf-8

from sanic import Request, HTTPResponse

from .base import BaseMiddleware


class CacheMiddleware(BaseMiddleware):
    """Redis中间件"""

    async def before_request(self, request: Request) -> None:
        """request绑定redis client"""
        request.ctx.cache_client = request.ctx.cache_client

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        """cache_client自动放回连接池，无须处理"""
        pass
