# coding=utf-8

from sanic import HTTPResponse, exceptions
from sanic.log import logger
from sanic.request import Request
from tortoise.exceptions import ValidationError, IntegrityError, FieldError

from core.response import response_fail
from .base import BaseExcHandler
from ..error_code import ErrorCode


class UnknownExcHandler(BaseExcHandler):
    """未知异常处理"""

    def get_exception(self) -> type[Exception]:
        return Exception

    def handle(self, request: Request, exception: Exception) -> HTTPResponse:
        logger.exception(exception)
        if isinstance(exception, exceptions.NotFound):
            return response_fail(ErrorCode.NotFound)
        elif isinstance(exception, ValidationError):
            return response_fail(ErrorCode.ParameterError)
        elif isinstance(exception, IntegrityError):
            return response_fail(ErrorCode.ParameterError)
        elif isinstance(exception, FieldError):
            return response_fail(ErrorCode.ParameterError)
        else:
            return response_fail(ErrorCode.ServerError)
