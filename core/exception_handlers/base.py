# coding=utf-8


import abc

from sanic.request import Request
from sanic import HTTPResponse


class BaseExcHandler(metaclass=abc.ABCMeta):
    """基础异常处理"""

    @abc.abstractmethod
    def get_exception(self) -> type[Exception]:
        """获取异常"""
        pass

    @abc.abstractmethod
    def handle(self, request: Request, exception: Exception) -> HTTPResponse:
        """异常处理"""
        pass
