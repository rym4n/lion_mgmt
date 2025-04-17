# coding=utf-8
import os

from sanic import Request, HTTPResponse
from sanic.log import logger

from libs.error_code.exception import CustomError
from libs.security.authentication import authenticate
from models import AuditLog
from .base import BaseMiddleware
from ..error_code import ErrorCode


class SecurityMiddleware(BaseMiddleware):
    """安全相关的控制"""

    async def before_request(self, request: Request) -> None:
        # 简单判断参数合法性
        if 'multipart' not in request.content_type and request.method == 'POST' and request.json is None:
            # todo: 正式版本之前保留log便于调试
            logger.info(f"请求 {request.path} 的参数不合法, body: {request.body}, headers: {request.headers}")
            raise CustomError(ErrorCode.InvalidJsonError)

        # 判断请求URI是否需要身份验证
        white_urls = ["/v1/user/login", "/health", "/v1/playbook/run", "/v1/tool_management/ws/unzip"]
        if os.environ.get("RY_ENV") == 'TEST':
            # 开发测试环境，放行docs文档页面路径
            white_urls.extend(["/docs", "/docs/openapi.json"])

        if request.path not in white_urls:
            await authenticate(request)

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        """统一记录操作日志"""
        if response.status == 200 and hasattr(request.ctx, "session"):
            # 写入数据库
            # logger.info(f"{request.client_ip} 访问 {request.endpoint} 成功, URI: {request.path}")
            # logger.info(f"会话信息: {request.ctx.session}")
            await AuditLog.create(username=request.ctx.session['name'],
                                  action="访问",
                                  module=request.endpoint,
                                  result="成功",
                                  uri=request.path,
                                  client_ip=request.client_ip,
                                  client_ua=request.headers.get('User-Agent', ''),
                                  request_data=request.json if 'multipart' not in request.content_type else None)
