# coding=utf-8
from asyncpg.exceptions import UniqueViolationError
from sanic import HTTPResponse, MethodNotAllowed, exceptions
from sanic.log import logger
from sanic.request import Request
from tortoise.exceptions import DoesNotExist, FieldError, IntegrityError, ValidationError

from core.response import response_fail

from ..error_code import ErrorCode
from .base import BaseExcHandler


class UnknownExcHandler(BaseExcHandler):
    """未知异常处理"""

    def get_exception(self) -> type[Exception]:
        return Exception

    def handle(self, request: Request, exception: Exception) -> HTTPResponse:
        if isinstance(exception, exceptions.NotFound) or isinstance(exception, DoesNotExist):
            return response_fail(ErrorCode.NOT_FOUND)
        elif isinstance(exception, exceptions.BadRequest) or isinstance(exception, ValidationError) or isinstance(exception, ValueError) or isinstance(exception, TypeError) or isinstance(exception, KeyError) or isinstance(exception, FieldError):
            logger.exception(exception)
            return response_fail(ErrorCode.PARAM_ERROR)
        elif isinstance(exception, IntegrityError) or isinstance(exception, UniqueViolationError):
            return response_fail(ErrorCode.DATA_ALREADY_EXISTS)
        elif isinstance(exception, MethodNotAllowed):
            return response_fail(ErrorCode.METHOD_NOT_ALLOWED)
        else:
            logger.exception(exception)
            return response_fail(ErrorCode.SERVER_ERROR, 500)
