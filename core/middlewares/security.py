# coding=utf-8
import uuid
from datetime import datetime

import orjson
from sanic import Request, HTTPResponse

from libs.kafka import aproduce
from libs.security.authentication import authenticate
from libs.utils import get_client_ip
from settings import KAFKA_SERVERS, AUDIT_TOPIC, TZ
from .base import BaseMiddleware


class SecurityMiddleware(BaseMiddleware):
    """安全相关的控制"""

    async def before_request(self, request: Request) -> None:
        # 判断请求URI是否需要身份验证
        white_urls = ["/user/login", "/health", "/system/oauth/status", "/system/platform", "/system/picture", "/user/oauth/login", "/user/oauth/callback", "/user/local-auth"]
        if request.path not in white_urls:
            await authenticate(request)

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        """统一记录操作日志"""
        data = None
        if hasattr(request.ctx, "session"):
            data = {
                "username": request.ctx.session["name"],
                "display_name": request.ctx.session["display_name"],
                "tenant_id": request.ctx.session["tenant_id"] if "tenant_id" in request.ctx.session else 0,
                "client_ip": request.ctx.session["ip"],
                "result": "成功" if response.status == 200 else "失败",
                "uri": request.path,
                "client_ua": request.headers.get("User-Agent", ""),
                "request_data": str(request.body) if "multipart" not in request.content_type else None,
            }
        elif request.path == "/user/login" and request.method == "POST":
            params = request.json
            if params:
                params["password"] = "******"
            data = {
                "username": request.json.get("username", ""),
                "display_name": request.json.get("username", ""),
                "tenant_id": request.json.get("tenant_id", 0),
                "client_ip": get_client_ip(request),
                "result": "成功" if response.status == 200 else "失败",
                "uri": request.path,
                "client_ua": request.headers.get("User-Agent", ""),
                "request_data": orjson.dumps(params).decode(),
            }
        elif request.path == "/user/oauth/callback":
            data = {
                "username": "",
                "display_name": "",
                "tenant_id": request.json.get("tenant_id", 0),
                "client_ip": get_client_ip(request),
                "result": "成功" if response.status < 400 else "失败",
                "uri": request.path,
                "client_ua": request.headers.get("User-Agent", ""),
                "request_data": "",
            }
        if data is not None:
            data["ctime"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z")
            data["id"] = str(uuid.uuid4())
            await aproduce(KAFKA_SERVERS, AUDIT_TOPIC, data)
