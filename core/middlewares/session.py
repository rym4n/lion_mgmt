# coding=utf-8

from sanic import Request, HTTPResponse
from sanic.log import logger

from libs.security.authentication import authenticate
from .base import BaseMiddleware


class SessionMiddleware(BaseMiddleware):
    """会话管理"""

    async def before_request(self, request: Request) -> None:
        # 判断请求URI是否需要身份验证
        logger.info(request.path)
        for path in ["/v1/user/login", "/v1/user//mfa-auth"]:
            if request.path.startswith(path):
                return

        await authenticate(request)

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        pass
