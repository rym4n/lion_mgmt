# coding=utf-8
import os

from sanic import Request, HTTPResponse

from libs.security.authentication import authenticate
from libs.utils import get_client_ip
from models import AuditLog
from .base import BaseMiddleware


class SecurityMiddleware(BaseMiddleware):
    """安全相关的控制"""

    async def before_request(self, request: Request) -> None:
        # 判断请求URI是否需要身份验证
        white_urls = ["/v1/user/login", "/health", "/v1/playbook/run", "/v1/warroom/ws"]
        if os.environ.get("RY_ENV") == 'TEST':
            # 开发测试环境，放行docs文档页面路径
            white_urls.extend(["/docs", "/docs/openapi.json"])

        if request.path not in white_urls:
            await authenticate(request)

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        """统一记录操作日志"""
        if response.status == 200 and hasattr(request.ctx, "session"):
            await AuditLog.create(username=request.ctx.session['name'], action="访问", module=request.endpoint, result="成功",
                                  uri=request.path, client_ip=get_client_ip(request), client_ua=request.headers.get('User-Agent', ''),
                                  request_data=request.json if 'multipart' not in request.content_type else None)
