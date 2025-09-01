# coding=utf-8
import uuid

from sanic import Request, HTTPResponse

from libs.kafka import aproduce
from libs.security.authentication import authenticate
from libs.utils import get_client_ip, get_time
from settings import KAFKA_SERVERS, AUDIT_TOPIC
from .base import BaseMiddleware


class SecurityMiddleware(BaseMiddleware):
    """安全相关的控制"""

    async def before_request(self, request: Request) -> None:
        # 判断请求URI是否需要身份验证
        white_urls = [
            "/user/login", "/health", '/system/oauth/status', '/system/platform', '/system/picture',
            '/user/oauth/login', '/user/oauth/callback', '/user/local-auth'
        ]
        if request.path not in white_urls:
            await authenticate(request)

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        """统一记录操作日志"""
        data = None
        if hasattr(request.ctx, "session"):
            params = request.json if 'multipart' not in request.content_type else None
            if params:
                for key in ['password', 'code', 'token']:
                    if key in params:
                        params[key] = '******'
            data = {
                "username": request.ctx.session['name'],
                "display_name": request.ctx.session['display_name'],
                "client_ip": request.ctx.session['ip'],
                "result": "成功" if response.status == 200 else "失败",
                "uri": request.path,
                "client_ua": request.headers.get('User-Agent', ''),
                "request_data": request.json if 'multipart' not in request.content_type else None
            }
        elif request.path == '/user/login' and request.method == 'POST':
            params = request.json
            if params:
                params['password'] = '******'
            data = {
                "username": request.json.get('username', ''),
                "display_name": request.json.get('username', ''),
                "client_ip": get_client_ip(request),
                "result": "成功" if response.status == 200 else "失败",
                "uri": request.path,
                "client_ua": request.headers.get('User-Agent', ''),
                "request_data": params
            }
        elif request.path == '/user/oauth/callback':
            params = request.args
            if params:
                params['code'] = '******'
            data = {
                "username": request.ctx.session['name'],
                "display_name": request.ctx.session['display_name'],
                "client_ip": request.ctx.session['ip'],
                "result": "成功" if response.status == 200 else "失败",
                "uri": request.path,
                "client_ua": request.headers.get('User-Agent', ''),
                "request_data": params
            }
        if data:
            data["ctime"] = get_time()
            data["utime"] = get_time()
            data["id"] = str(uuid.uuid4())
            await aproduce(KAFKA_SERVERS, AUDIT_TOPIC, data)
