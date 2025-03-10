# coding=utf-8

"""响应相关配置"""

from datetime import datetime
from decimal import Decimal
from json import JSONEncoder, dumps
from typing import Any, Optional

from sanic import HTTPResponse
from sanic import json

from .error_code import ErrorCode


class ExtJsonEncoder(JSONEncoder):
    """扩展json编码器"""

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return o.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def response_ok(data: Any = None) -> HTTPResponse:
    """成功返回

    :param data: 数据
    :return:
    """
    # 错误码
    code: int = 0
    # 内容
    content: dict[str, Any] = dict(code=code, data=data)
    return json(content, dumps=dumps, cls=ExtJsonEncoder)


def response_fail(enum: Optional[ErrorCode] = None):
    """失败返回

    :param enum: 错误码枚举类
    :return:
    """
    if enum is None:
        enum = ErrorCode.NotFound
    # 错误码
    code: int = enum.code
    # 错误信息
    message: str = enum.message
    # 内容
    content: dict[int, Any] = dict(code=code, message=message)
    return json(content, dumps=dumps, cls=ExtJsonEncoder)
