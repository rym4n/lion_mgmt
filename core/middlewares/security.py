# coding=utf-8
import uuid
from datetime import datetime
from urllib.parse import urlsplit

import orjson
from sanic import HTTPResponse, Request
from sanic_ext.extensions.openapi.builders import OperationStore

from core.error_code import ErrorCode
from core.response import response_fail
from libs.kafka import aproduce
from libs.security.authentication import authenticate
from libs.security.authorization import check_permissions
from libs.utils import get_client_ip
from settings import AUDIT_TOPIC, TZ

from .base import BaseMiddleware


UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _normalized_origin(value: str | None) -> tuple[str, str, int] | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname.casefold(), port


def _effective_request_origin(request: Request) -> tuple[str, str, int] | None:
    forwarded_proto = request.headers.get("X-Forwarded-Proto")
    scheme = (
        (forwarded_proto or getattr(request, "scheme", "http")).split(",", 1)[0].strip()
    )
    host = (request.headers.get("Host") or getattr(request, "host", "")).strip()
    return _normalized_origin(f"{scheme}://{host}")


def _is_cross_origin_unsafe_request(request: Request) -> bool:
    if getattr(request, "method", "GET").upper() not in UNSAFE_HTTP_METHODS:
        return False
    origin = request.headers.get("Origin")
    if not origin:
        return False
    return _normalized_origin(origin) != _effective_request_origin(request)


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

    async def before_request(self, request: Request) -> HTTPResponse | None:
        if _is_cross_origin_unsafe_request(request):
            return response_fail(ErrorCode.FORBIDDEN, status=403)

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
            "/agent/report/schedule/internal/",
            "/digital_employee/internal/",
            "/digital_employee/runtime/",
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
