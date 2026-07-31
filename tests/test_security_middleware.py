# coding=utf-8
import asyncio
from types import SimpleNamespace

import orjson
import pytest

from core.middlewares import security


def _run(coro):
    return asyncio.run(coro)


def _capture_events(monkeypatch):
    events = []

    async def capture(_topic, data):
        events.append(data)

    monkeypatch.setattr(security, "aproduce", capture)
    return events


def _request(
    path="/user/new",
    *,
    method="POST",
    origin=None,
    host="192.168.12.254",
    scheme="https",
):
    headers = {"Host": host, "X-Forwarded-Proto": scheme}
    if origin is not None:
        headers["Origin"] = origin
    return SimpleNamespace(
        path=path,
        method=method,
        headers=headers,
        host=host,
        scheme=scheme,
    )


def test_cross_origin_unsafe_request_is_rejected_before_authentication(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)

    response = _run(
        security.SecurityMiddleware().before_request(
            _request(origin="https://evil.com")
        )
    )

    assert response.status == 403
    assert orjson.loads(response.body) == {"code": 4005, "message": "禁止访问"}
    assert called_paths == []


def test_same_origin_unsafe_request_still_authenticates(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)

    response = _run(
        security.SecurityMiddleware().before_request(
            _request(origin="https://192.168.12.254")
        )
    )

    assert response is None
    assert called_paths == ["/user/new"]


def test_forged_forwarded_host_does_not_bypass_cross_origin_rejection(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)
    request = _request(origin="https://evil.com")
    request.headers["X-Forwarded-Host"] = "evil.com"

    response = _run(security.SecurityMiddleware().before_request(request))

    assert response.status == 403
    assert called_paths == []


def test_non_browser_unsafe_request_without_origin_still_authenticates(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)

    response = _run(security.SecurityMiddleware().before_request(_request()))

    assert response is None
    assert called_paths == ["/user/new"]


def test_cross_origin_safe_request_keeps_iframe_login_flow(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)

    response = _run(
        security.SecurityMiddleware().before_request(
            _request(
                path="/user/iframe-login",
                method="GET",
                origin="https://trusted-embed.example",
            )
        )
    )

    assert response is None
    assert called_paths == []


def test_system_picture_only_allows_anonymous_get(monkeypatch):
    authenticated_requests = []

    async def fake_authenticate(request):
        authenticated_requests.append((request.method, request.path))

    monkeypatch.setattr(security, "authenticate", fake_authenticate)
    middleware = security.SecurityMiddleware()

    _run(middleware.before_request(_request(path="/system/picture", method="GET")))
    _run(middleware.before_request(_request(path="/system/picture", method="DELETE")))

    assert authenticated_requests == [("DELETE", "/system/picture")]


def test_digital_employee_internal_routes_skip_session_auth(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)
    middleware = security.SecurityMiddleware()

    _run(
        middleware.before_request(
            SimpleNamespace(path="/digital_employee/internal/employee/dispatch")
        )
    )
    _run(
        middleware.before_request(
            SimpleNamespace(path="/digital_employee/runtime/cognee/refresh-jwt")
        )
    )

    assert called_paths == []


def test_regular_digital_employee_routes_still_require_session_auth(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)
    middleware = security.SecurityMiddleware()

    _run(
        middleware.before_request(
            SimpleNamespace(path="/digital_employee/employee/list")
        )
    )

    assert called_paths == ["/digital_employee/employee/list"]


def test_digital_employee_bootstrap_route_requires_session_auth(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)
    middleware = security.SecurityMiddleware()

    _run(
        middleware.before_request(
            SimpleNamespace(path="/system/bootstrap/digital-employee/package/import")
        )
    )

    assert called_paths == ["/system/bootstrap/digital-employee/package/import"]


@pytest.mark.parametrize(
    ("status", "result"),
    ((200, "成功"), (202, "成功"), (302, "失败"), (400, "失败")),
)
def test_user_audit_classifies_every_http_outcome(monkeypatch, status, result):
    request = SimpleNamespace(
        ctx=SimpleNamespace(
            session={
                "name": "user",
                "display_name": "User",
                "tenant_id": "tenant-1",
                "ip": "192.0.2.10",
            }
        ),
        content_type="application/json",
        body=b"{}",
        path="/user/new",
        method="POST",
        headers={},
    )
    events = _capture_events(monkeypatch)

    _run(
        security.SecurityMiddleware().before_response(
            request, SimpleNamespace(status=status)
        )
    )
    assert events[0]["result"] == result


@pytest.mark.parametrize(
    ("path", "status", "result", "action"),
    (
        ("/user/login", 202, "成功", "登录"),
        ("/user/login", 302, "失败", "登录"),
        ("/user/login", 400, "失败", "登录"),
        ("/user/oauth/callback", 202, "成功", "OAuth回调"),
        ("/user/oauth/callback", 302, "失败", "OAuth回调"),
        ("/user/oauth/callback", 400, "失败", "OAuth回调"),
    ),
)
def test_login_and_oauth_audit_classify_outcomes_without_secrets(
    monkeypatch, path, status, result, action
):
    secret = "do-not-audit-password"
    request = SimpleNamespace(
        ctx=SimpleNamespace(),
        path=path,
        method="POST",
        headers={},
        ip="192.0.2.10",
        json={"username": "user", "password": secret, "tenant_id": "tenant-1"},
    )
    events = _capture_events(monkeypatch)

    _run(
        security.SecurityMiddleware().before_response(
            request, SimpleNamespace(status=status)
        )
    )
    assert events[0]["result"] == result
    assert events[0]["action"] == action
    if path == "/user/login":
        assert '"password":"******"' in events[0]["request_data"]
        assert secret not in events[0]["request_data"]
