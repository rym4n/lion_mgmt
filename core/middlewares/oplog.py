# coding=utf-8

from sanic import Request, HTTPResponse

from models.audit import Audit
from .base import BaseMiddleware


class OplogMiddleware(BaseMiddleware):
    """统一记录操作日志"""

    async def before_request(self, request: Request) -> None:
        pass

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        if response.status == 200:
            # 写入数据库
            await Audit.create(user_id=request.ctx.session['sub'],
                               action="访问",
                               module=request.endpoint,
                               result="成功",
                               uri=request.path,
                               client_ip=request.client_ip,
                               client_ua=request.headers.get('User-Agent', ''))
