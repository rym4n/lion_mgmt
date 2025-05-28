# coding=utf-8
import os

from sanic import Request, HTTPResponse

from libs.security.authentication import authenticate
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
        if hasattr(request.ctx, "session"):
            params = request.json if 'multipart' not in request.content_type else None
            for key in ['password', 'code', 'token']:
                if key in params:
                    params[key] = '******'
            await AuditLog.create(username=request.ctx.session['name'], display_name=request.ctx.session['display_name'], client_ip=request.ctx.session['ip'],
                                  result="成功" if response.status == 200 else "失败", uri=request.path, client_ua=request.headers.get('User-Agent', ''),
                                  request_data=request.json if 'multipart' not in request.content_type else None)
