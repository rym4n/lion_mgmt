# coding=utf-8
import uuid
from datetime import datetime

import orjson
from sanic import HTTPResponse, Request
from sanic_ext.extensions.openapi.builders import OperationStore

from libs.kafka import aproduce
from libs.security.authentication import authenticate
from libs.security.authorization import check_permissions
from libs.utils import get_client_ip
from settings import AUDIT_TOPIC, TZ

from .base import BaseMiddleware


def _get_openapi_summary(request: Request) -> str:
    route = getattr(request, "route", None)
    handler = getattr(route, "handler", None)
    if handler is None:
        return ""

    try:
        op_builder = OperationStore().get(handler)
    except Exception:
        return ""

    if not op_builder:
        return ""

    summary = getattr(op_builder, "summary", None)
    return summary or "访问"


class SecurityMiddleware(BaseMiddleware):
    """安全相关的控制"""

    async def before_request(self, request: Request) -> None:
        # 判断请求URI是否需要身份验证
        white_urls = [
            "/user/login",
            "/health",
            "/system/oauth/status",
            "/system/platform",
            "/system/picture",
            "/user/oauth/login",
            "/user/oauth/callback",
            "/user/local-auth",
            "/user/iframe-login",
            "/approve/callback",
        ]
        white_prefixes = [
            "/system/down-file/",
        ]
        if request.path not in white_urls and not any(request.path.startswith(prefix) for prefix in white_prefixes):
            await authenticate(request)
            if request.path == "/docs" or request.path.startswith("/docs/"):
                await check_permissions(request, "apidoc:r")

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        """统一记录操作日志"""
        data = None

        if hasattr(request.ctx, "session"):
            param = None
            if "multipart" not in request.content_type:
                param = request.body.decode() if isinstance(request.body, bytes) else request.body
            data = {
                "username": request.ctx.session["name"],
                "display_name": request.ctx.session["display_name"],
                "tenant_id": (request.ctx.session["tenant_id"] if "tenant_id" in request.ctx.session else 0),
                "client_ip": request.ctx.session["ip"],
                "result": "成功" if response.status == 200 else "失败",
                "uri": request.path,
                "client_ua": request.headers.get("User-Agent", ""),
                "request_data": param,
                "action": _get_openapi_summary(request),
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
                "action": "登录",
            }
        elif request.path == "/user/oauth/callback":
            data = {
                "username": "",
                "display_name": "",
                "tenant_id": "",
                "client_ip": get_client_ip(request),
                "result": "成功" if response.status < 400 else "失败",
                "uri": request.path,
                "client_ua": request.headers.get("User-Agent", ""),
                "request_data": "",
                "action": "OAuth回调",
            }
        if data is not None:
            data["ctime"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z")
            data["id"] = str(uuid.uuid4())
            data["log_type"] = "aioe_audit"
            await aproduce(AUDIT_TOPIC, data)
