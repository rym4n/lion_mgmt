# coding=utf-8

from sanic import Request, HTTPResponse

from .base import BaseMiddleware


class SessionMiddleware(BaseMiddleware):
    """会话管理"""

    async def before_request(self, request: Request) -> None:
        pass

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        if hasattr(request.ctx, "session"):
            if getattr(request.ctx.session, "is_refresh", False):
                request.ctx.session.encode(response)
            elif not request.ctx.session.active:
                response.delete_cookie(f"aioe_{request.ctx.session.__class__.__name__[:7].lower()}")
