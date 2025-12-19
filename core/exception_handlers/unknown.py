# coding=utf-8
from asyncpg.exceptions import UniqueViolationError
from sanic import HTTPResponse, exceptions, MethodNotAllowed
from sanic.log import logger
from sanic.request import Request
from tortoise.exceptions import ValidationError, IntegrityError, FieldError, DoesNotExist

from core.response import response_fail
from .base import BaseExcHandler
from ..error_code import ErrorCode


class UnknownExcHandler(BaseExcHandler):
    """未知异常处理"""

    def get_exception(self) -> type[Exception]:
        return Exception

    def handle(self, request: Request, exception: Exception) -> HTTPResponse:
        if isinstance(exception, exceptions.NotFound) or isinstance(exception, DoesNotExist):
            return response_fail(ErrorCode.NotFound)
        elif (
            isinstance(exception, exceptions.BadRequest)
            or isinstance(exception, ValidationError)
            or isinstance(exception, ValueError)
            or isinstance(exception, TypeError)
            or isinstance(exception, KeyError)
            or isinstance(exception, FieldError)
        ):
            logger.exception(exception)
            return response_fail(ErrorCode.ParameterError)
        elif isinstance(exception, IntegrityError) or isinstance(exception, UniqueViolationError):
            return response_fail(ErrorCode.DataAlreadyExists)
        elif isinstance(exception, MethodNotAllowed):
            return response_fail(ErrorCode.MethodNotAllowed)
        else:
            logger.exception(exception)
            return response_fail(ErrorCode.ServerError, 500)
