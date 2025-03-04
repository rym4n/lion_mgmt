# coding=utf-8


from sanic import HTTPResponse
from sanic.request import Request

from core.response import response_fail
from libs.error_code.exception import CustomError
from .base import BaseExcHandler


class ErrorCodeExcHandler(BaseExcHandler):
    """错误码异常处理"""

    def get_exception(self) -> type[CustomError]:
        return CustomError

    def handle(self, request: Request, exception: CustomError) -> HTTPResponse:
        return response_fail(enum=exception.enum)
