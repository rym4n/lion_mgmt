# coding=utf-8
"""
响应工具模块

提供统一的接口响应封装函数，包括成功和失败返回格式。
"""
from typing import Any, Optional

import orjson
from sanic import HTTPResponse
from sanic import json
from sanic.log import logger

from .error_code import ErrorCode


def response_ok(data: Any = None) -> HTTPResponse:
    """成功返回

    :param data: 数据
    :return:
    """
    # 内容
    content: dict[str, Any] = {"code": 0, "data": data}
    try:
        orjson.dumps(content)
    except Exception as e:
        logger.error(f"response_ok dumps error: {e} | content: {content}")
    return json(content, dumps=orjson.dumps)


# noinspection PyTypeChecker
def response_fail(enum: Optional[ErrorCode] = None, status: int = 400):
    """失败返回

    :param status: 指定状态码，默认400
    :param enum: 错误码枚举类
    :return:
    """
    if enum is None:
        enum = ErrorCode.NOT_FOUND
    # 错误码
    code: int = enum.code
    # 错误信息
    message: str = enum.message
    # 内容
    content: dict[str, Any] = {"code": code, "message": message}
    return json(content, status=status, dumps=orjson.dumps)
