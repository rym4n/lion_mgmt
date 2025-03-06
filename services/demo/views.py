# coding=utf-8
from typing import Any

from sanic import HTTPResponse
from sanic import Request, Blueprint
from sanic.log import logger

from core.response import response_ok

demo = Blueprint('demo', url_prefix='demo/v1')
_all__ = ["demo"]


@demo.route("/hello", methods=["GET"])
async def hello(request: Request) -> HTTPResponse:
    return response_ok({'message': 'hello world!'})


@demo.route("/error", methods=["GET"])
async def error(request: Request) -> HTTPResponse:
    """测试错误码异常"""
    from core.error_code import ErrorCode
    from libs.error_code.exception import CustomError
    raise CustomError(ErrorCode.Forbidden)


@demo.route("/error/unknown", methods=["GET"])
async def unknown_error(request: Request) -> HTTPResponse:
    """测试未知异常"""
    raise ValueError("Test exception")


@demo.route("/cache/test", methods=["GET"])
async def test_cache(request: Request) -> HTTPResponse:
    """测试缓存"""
    cache_client = request.ctx.cache_client
    logger.info(request.args)
    key = "counter"
    current_value = await cache_client.increment(key, 1)
    await cache_client.expire(key, 30)
    data: dict[str, Any] = {
        "num": current_value
    }
    return response_ok(data)
