# coding=utf-8

from sanic import Request, HTTPResponse

from libs.error_code.exception import CustomError
from libs.security.authentication import authenticate
from models import AuditLog
from .base import BaseMiddleware
from ..error_code import ErrorCode


class SecurityMiddleware(BaseMiddleware):
    """安全相关的控制"""

    async def before_request(self, request: Request) -> None:
        # 判断请求URI是否需要身份验证
        # todo: 上生产的时候需要删掉 /docs
        for path in ["/v1/user/login", "/v1/user/mfa-auth", "/docs", "/docs/openapi.json"]:
            if request.path == path:
                return

        await authenticate(request)

        # 简单判断参数合法性
        if 'multipart' not in request.content_type and request.json is None:
            raise CustomError(ErrorCode.InvalidJsonError)

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        """统一记录操作日志"""
        if response.status == 200:
            # 写入数据库
            await AuditLog.create(username=request.ctx.session['name'],
                                  action="访问",
                                  module=request.endpoint,
                                  result="成功",
                                  uri=request.path,
                                  client_ip=request.client_ip,
                                  client_ua=request.headers.get('User-Agent', ''))
