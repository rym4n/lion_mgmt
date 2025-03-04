# coding=utf-8

from sanic import HTTPResponse, exceptions
from sanic.log import logger
from sanic.request import Request

from core.response import response_fail
from .base import BaseExcHandler
from ..error_code import ErrorCode


class UnknownExcHandler(BaseExcHandler):
    """未知异常处理"""

    def get_exception(self) -> type[Exception]:
        return Exception

    def handle(self, request: Request, exception: Exception) -> HTTPResponse:
        if isinstance(exception, exceptions.NotFound):
            return response_fail(ErrorCode.NotFound)
        else:
            logger.exception(exception)
            return response_fail(ErrorCode.ServerError)
