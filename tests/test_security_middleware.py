# coding=utf-8
import asyncio
from types import SimpleNamespace

from core.middlewares import security


def _run(coro):
    return asyncio.run(coro)


def test_digital_employee_internal_routes_skip_session_auth(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)
    middleware = security.SecurityMiddleware()

    _run(middleware.before_request(SimpleNamespace(path="/digital_employee/internal/employee/dispatch")))
    _run(middleware.before_request(SimpleNamespace(path="/digital_employee/runtime/cognee/refresh-jwt")))

    assert called_paths == []


def test_regular_digital_employee_routes_still_require_session_auth(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)
    middleware = security.SecurityMiddleware()

    _run(middleware.before_request(SimpleNamespace(path="/digital_employee/employee/list")))

    assert called_paths == ["/digital_employee/employee/list"]
