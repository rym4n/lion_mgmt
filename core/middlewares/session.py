# coding=utf-8

from sanic import Request, HTTPResponse

from libs.security.authentication import authenticate
from .base import BaseMiddleware


class SessionMiddleware(BaseMiddleware):
    """会话管理"""

    async def before_request(self, request: Request) -> None:
        # 判断请求URI是否需要身份验证
        # todo: 上生产的时候需要删掉 /docs
        for path in ["/v1/user/login", "/v1/user/mfa-auth", "/docs", "/docs/openapi.json"]:
            if request.path == path:
                return

        await authenticate(request)

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        pass
