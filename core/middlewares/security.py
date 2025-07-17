# coding=utf-8

from sanic import Request, HTTPResponse

from libs.security.authentication import authenticate
from libs.utils import get_client_ip
from models import AuditLog
from .base import BaseMiddleware


class SecurityMiddleware(BaseMiddleware):
    """安全相关的控制"""

    async def before_request(self, request: Request) -> None:
        # 判断请求URI是否需要身份验证
        white_urls = ["/user/login", "/health", '/system/oauth/status', '/system/platform', '/user/oauth/login', '/user/oauth/callback']

        if request.path == '/license/ai/update' and get_client_ip(request) == '127.0.0.1':
            # AI更新接口，允许本地调用
            return

        if request.path not in white_urls:
            await authenticate(request)

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        """统一记录操作日志"""
        if hasattr(request.ctx, "session"):
            params = request.json if 'multipart' not in request.content_type else None
            if params:
                for key in ['password', 'code', 'token']:
                    if key in params:
                        params[key] = '******'
            await AuditLog.create(username=request.ctx.session['name'], display_name=request.ctx.session['display_name'], client_ip=request.ctx.session['ip'],
                                  result="成功" if response.status == 200 else "失败", uri=request.path, client_ua=request.headers.get('User-Agent', ''),
                                  request_data=request.json if 'multipart' not in request.content_type else None)
