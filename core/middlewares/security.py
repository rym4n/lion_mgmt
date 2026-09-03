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


def _is_success_status(status: object) -> bool:
    return type(status) is int and 200 <= status < 300


SENSITIVE_REQUEST_FIELDS = frozenset(
    {
        "password",
        "current_password",
        "old_password",
        "new_password",
        "confirm_password",
        "mfa_code",
    }
)


def _redact_sensitive_request_data(value: str | bytes | None):
    if not value:
        return value
    try:
        payload = orjson.loads(value)
    except orjson.JSONDecodeError:
        return value.decode() if isinstance(value, bytes) else value

    def redact(item):
        if isinstance(item, dict):
            return {
                key: (
                    "******"
                    if str(key).casefold() in SENSITIVE_REQUEST_FIELDS
                    else redact(child)
                )
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item

    return orjson.dumps(redact(payload)).decode()


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


def _normal_request_data(request: Request) -> str | bytes | None:
    if "multipart" in (getattr(request, "content_type", "") or ""):
        return None
    return request.body.decode() if isinstance(request.body, bytes) else request.body


def _redacted_user_request_data(request: Request) -> str | bytes | None:
    sensitive_user_paths = {
        "/user/change-password",
        "/user/edit",
        "/user/login",
        "/user/mfa-auth",
        "/user/new",
        "/user/sensitive-mfa-auth",
        "/user/update",
    }
    if request.path in sensitive_user_paths:
        try:
            payload = request.json
        except Exception:  # noqa: BLE001 - malformed bodies are handled by the API layer.
            payload = None
        if type(payload) is dict:
            redacted = orjson.loads(
                _redact_sensitive_request_data(orjson.dumps(payload))
            )
            if request.path in {"/user/mfa-auth", "/user/sensitive-mfa-auth"}:
                if "code" in redacted:
                    redacted["code"] = "******"
            return orjson.dumps(redacted).decode()
    return _redact_sensitive_request_data(_normal_request_data(request))


def _session_audit_projection(
    session: dict,
    *,
    status: object,
    uri: str,
    client_ua: str,
    request_data: str | bytes | None,
    action: str,
) -> dict:
    return {
        "username": session["name"],
        "display_name": session["display_name"],
        "tenant_id": session.get("tenant_id", 0),
        "client_ip": session["ip"],
        "result": "成功" if _is_success_status(status) else "失败",
        "uri": uri,
        "client_ua": client_ua,
        "request_data": request_data,
        "action": action,
    }


def _login_audit_projection(
    params: dict,
    *,
    client_ip: str,
    status: object,
    uri: str,
    client_ua: str,
) -> dict:
    return {
        "username": params.get("username", ""),
        "display_name": params.get("username", ""),
        "tenant_id": params.get("tenant_id", 0),
        "client_ip": client_ip,
        "result": "成功" if _is_success_status(status) else "失败",
        "uri": uri,
        "client_ua": client_ua,
        "request_data": _redact_sensitive_request_data(orjson.dumps(params)),
        "action": "登录",
    }


def _oauth_audit_projection(
    *, client_ip: str, status: object, uri: str, client_ua: str
) -> dict:
    return {
        "username": "",
        "display_name": "",
        "tenant_id": "",
        "client_ip": client_ip,
        "result": "成功" if _is_success_status(status) else "失败",
        "uri": uri,
        "client_ua": client_ua,
        "request_data": "",
        "action": "OAuth回调",
    }


def _user_audit_projection(request: Request, response: HTTPResponse) -> dict | None:
    if hasattr(request.ctx, "session"):
        return _session_audit_projection(
            request.ctx.session,
            status=response.status,
            uri=request.path,
            client_ua=request.headers.get("User-Agent", ""),
            request_data=_redacted_user_request_data(request),
            action=_get_openapi_summary(request),
        )
    if request.path == "/user/login" and request.method == "POST":
        params = request.json
        return _login_audit_projection(
            params,
            client_ip=get_client_ip(request),
            status=response.status,
            uri=request.path,
            client_ua=request.headers.get("User-Agent", ""),
        )
    if request.path == "/user/oauth/callback":
        return _oauth_audit_projection(
            client_ip=get_client_ip(request),
            status=response.status,
            uri=request.path,
            client_ua=request.headers.get("User-Agent", ""),
        )
    return None


async def _publish_audit(data: dict) -> None:
    data["ctime"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z")
    data["id"] = str(uuid.uuid4())
    data["log_type"] = "aioe_audit"
    await aproduce(AUDIT_TOPIC, data)


class SecurityMiddleware(BaseMiddleware):
    """安全相关的控制"""

    async def before_request(self, request: Request) -> HTTPResponse | None:
        if _is_cross_origin_unsafe_request(request):
            return response_fail(ErrorCode.FORBIDDEN, status=403)
        white_urls = [
            "/user/login",
            "/health",
            "/system/oauth/status",
            "/system/platform",
            "/user/oauth/login",
            "/user/oauth/callback",
            "/user/local-auth",
            "/user/iframe-login",
            "/approve/callback",
            "/ticket/callback",
        ]
        white_method_urls = {("GET", "/system/picture")}
        white_prefixes = [
            "/system/down-file/",
            "/agent/report/schedule/internal/",
            "/digital_employee/internal/",
            "/digital_employee/runtime/",
            # lion_agent 的文档入库状态回调。它带的是服务间 internal token，
            # 没有用户 session，被这里拦掉就会返回 4027，文档状态永远写不回来
            # ——表现为「一直 processing，30 分钟后显示索引超时」。
            # 路由自身用 _check_internal 校验 X-Internal-Token。
            "/knowledge/internal/",
        ]
        request_method = getattr(request, "method", "GET").upper()
        if (
            request.path not in white_urls
            and (request_method, request.path) not in white_method_urls
            and not any(request.path.startswith(prefix) for prefix in white_prefixes)
        ):
            await authenticate(request)
            if request.path == "/docs" or request.path.startswith("/docs/"):
                await check_permissions(request, "apidoc:r")

    async def before_response(self, request: Request, response: HTTPResponse) -> None:
        """统一记录操作日志"""
        data = _user_audit_projection(request, response)
        if data is not None:
            await _publish_audit(data)
