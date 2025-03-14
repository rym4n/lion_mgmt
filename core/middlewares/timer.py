# coding=utf-8

import time

from sanic import Request, HTTPResponse
from sanic.log import logger

from .base import BaseMiddleware


class TimerMiddleware(BaseMiddleware):
    """计时中间件，默认会把响应耗时超过1秒的打印出来"""

    threshold: float = 0.5

    async def before_request(self, request: Request) -> None:
        request.ctx.start_time = time.time()

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        diff = time.time() - request.ctx.start_time
        if diff > self.threshold:
            logger.warning('【注意】 %s 响应时间较长: %.3fs' % (request.name, diff))
