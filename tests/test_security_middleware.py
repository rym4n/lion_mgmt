# coding=utf-8
import asyncio
import re
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
            SimpleNamespace(path="/digital_employee/runtime/memory/maintain")
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


def test_knowledge_ingest_callback_skips_session_auth(monkeypatch):
    """lion_agent 的入库状态回调必须免 session 鉴权。

    回归：白名单曾漏掉 /knowledge/internal/，回调被中间件拦下返回 4027，
    文档状态永远写不回来——用户看到的是「一直 processing，30 分钟后索引超时」，
    而 lion_agent 侧其实解析、embedding、向量写入全部成功。
    """
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)

    _run(
        security.SecurityMiddleware().before_request(
            SimpleNamespace(path="/knowledge/internal/documents/doc-1/status")
        )
    )

    assert called_paths == []


def test_regular_knowledge_routes_still_require_session_auth(monkeypatch):
    called_paths = []

    async def fake_authenticate(request):
        called_paths.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)

    _run(
        security.SecurityMiddleware().before_request(
            SimpleNamespace(path="/knowledge/document/list")
        )
    )

    assert called_paths == ["/knowledge/document/list"]


def _internal_token_routes():
    """静态扫描各 views.py，列出所有靠 _check_internal 鉴权的路由完整路径。

    刻意走 AST 而不是读蓝图的路由表：蓝图注册进 app 后 Sanic 会把
    _future_routes 搬到 routes，扫哪个取决于同批次里有没有别的测试建过 app，
    结果会随执行顺序漂移。源码是稳定的。
    """
    import ast
    from pathlib import Path

    services_dir = Path(security.__file__).resolve().parents[2] / "services"
    routes = []
    for views_file in sorted(services_dir.glob("*/views.py")):
        tree = ast.parse(views_file.read_text(encoding="utf-8"))

        # 先取本模块的蓝图前缀：Blueprint("x", url_prefix="/x")
        prefix = ""
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "Blueprint":
                continue
            for kw in node.keywords:
                if kw.arg == "url_prefix" and isinstance(kw.value, ast.Constant):
                    prefix = str(kw.value.value).rstrip("/")
            break

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_src = ast.unparse(node)
            if "_check_internal" not in body_src:
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                attr = getattr(decorator.func, "attr", "")
                if attr not in {"post", "get", "put", "patch", "delete"}:
                    continue
                if not decorator.args or not isinstance(
                    decorator.args[0], ast.Constant
                ):
                    continue
                uri = str(decorator.args[0].value)
                # 把 <document_id> 这类占位换成具体值，白名单是前缀匹配、不受影响
                uri = re.sub(r"<[^>]+>", "x", uri)
                routes.append(f"{prefix}/{uri.lstrip('/')}")
    return routes


def test_every_internal_token_route_is_exempt_from_session_auth(monkeypatch):
    """结构性护栏：凡是用 _check_internal 做服务间鉴权的路由，都必须在白名单里。

    两者错配时故障是静默的——调用方收到 4027「会话无效」，而路由的
    docstring 明写着「不走用户 session」，看代码根本发现不了。逐条比对，
    以后新增内部路由漏加白名单会在这里直接失败。
    """
    authenticated = []

    async def fake_authenticate(request):
        authenticated.append(request.path)

    monkeypatch.setattr(security, "authenticate", fake_authenticate)
    middleware = security.SecurityMiddleware()

    internal_paths = _internal_token_routes()

    # 至少要扫到已知的那些内部路由，否则说明扫描逻辑本身失效了
    assert len(internal_paths) >= 5, f"扫描到的内部路由过少: {internal_paths}"
    assert any(
        "/knowledge/internal/" in path for path in internal_paths
    ), f"未扫到知识库回调路由: {internal_paths}"

    for path in internal_paths:
        _run(middleware.before_request(SimpleNamespace(path=path)))

    assert authenticated == [], f"这些内部路由仍在要求 session 鉴权: {authenticated}"


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


def test_audit_request_data_recursively_redacts_password_fields():
    payload = {
        "username": "operator",
        "password": "new-secret",
        "current_password": "old-secret",
        "nested": [{"confirm_password": "new-secret", "safe": "visible"}],
    }

    redacted = orjson.loads(
        security._redact_sensitive_request_data(orjson.dumps(payload))
    )

    assert redacted == {
        "username": "operator",
        "password": "******",
        "current_password": "******",
        "nested": [{"confirm_password": "******", "safe": "visible"}],
    }
    assert payload["password"] == "new-secret"


def test_resource_health_check_audit_redacts_arbitrary_resource_values():
    request = SimpleNamespace(
        path="/tools/resource/health-check",
        json={
            "tool_name": "demo",
            "resources": {
                "api_token": {"data_type": "string", "value": "secret-token", "default_value": "default-secret"},
                "host": {"data_type": "string", "value": "https://example.test"},
            },
        },
        body=b"",
    )
    redacted = orjson.loads(security._redacted_user_request_data(request))
    assert redacted["resources"]["api_token"]["value"] == "******"
    assert redacted["resources"]["host"]["value"] == "******"
    assert "secret-token" not in security._redacted_user_request_data(request)
    assert "default-secret" not in security._redacted_user_request_data(request)
    assert request.json["resources"]["api_token"]["value"] == "secret-token"


def test_malformed_resource_health_check_audit_never_records_raw_payload():
    request = SimpleNamespace(
        path="/tools/resource/health-check", json=["malformed-secret"],
        body=b'["malformed-secret"]',
    )
    assert "malformed-secret" not in (security._redacted_user_request_data(request) or "")


def test_audit_request_data_preserves_non_json_payload():
    assert security._redact_sensitive_request_data("plain text") == "plain text"
