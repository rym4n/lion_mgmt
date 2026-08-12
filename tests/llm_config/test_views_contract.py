# coding=utf-8
"""路由契约测试：权限、License 门禁、审计埋点、脱敏，逐条钉死。"""
import asyncio
import inspect
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[2] / "services" / "llm_config" / "views.py"
APP_FACTORY = Path(__file__).resolve().parents[2] / "services" / "app_factory.py"


def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def test_blueprint_declared_with_expected_prefix():
    src = _src()
    assert 'Blueprint("llm_config"' in src
    assert 'url_prefix="/llm-config"' in src


def test_all_six_routes_present():
    src = _src()
    for path in ("/list", "/create", "/update", "/delete", "/set-purpose", "/test"):
        assert f'@llm_config_bp.post("{path}")' in src, f"缺少路由 {path}"


def test_every_route_has_permission_decorator():
    """每个路由都必须有 requires_permission，权限码前缀为 ai_config。"""
    src = _src()
    route_count = src.count("@llm_config_bp.post(")
    perm_count = len(re.findall(r'@requires_permission\("ai_config:[crud]"\)', src))
    assert perm_count == route_count, f"{route_count} 个路由只有 {perm_count} 个带权限装饰器"


def test_every_handler_calls_license_gate():
    """check_ai_permission() 是 License 第二道防线：蓝图在启动时注册，
    License 到期不会自动注销路由，所以 handler 内必须再校验一次。"""
    src = _src()
    route_count = src.count("@llm_config_bp.post(")
    assert src.count("check_ai_permission()") >= route_count


def test_every_route_has_openapi_summary_for_audit():
    """没有 summary，SecurityMiddleware 自动埋点的审计日志 action 字段就是空的。"""
    src = _src()
    route_count = src.count("@llm_config_bp.post(")
    assert src.count("@openapi.definition(") == route_count


def test_registered_under_ai_bp_license_key():
    """必须挂在 ai_bp 这个 License 模块下，与菜单行的 bp 字段一致。
    单独用 llm_config_bp 作 key 会导致 License 里没这个模块、路由永不注册。"""
    factory = APP_FACTORY.read_text(encoding="utf-8")
    assert "from services.llm_config.views import llm_config_bp" in factory
    assert re.search(r'"ai_bp":\s*\(ai_bp,\s*llm_config_bp\)', factory), \
        'licensed 字典应为 "ai_bp": (ai_bp, llm_config_bp)'


def test_views_never_return_raw_api_key(monkeypatch):
    """编辑连通性测试可读取真实密钥，但密钥不得进入响应体。"""
    sys.modules.pop("models", None)
    import models
    from services.llm_config import views

    secret = "sk-live-abcdef"
    captured = {}

    class FakeLlmModelConfig:
        @staticmethod
        async def get_or_none(**_conditions):
            return SimpleNamespace(api_key=secret)

    async def fake_test_connectivity(_base_url, api_key, _model_name):
        assert api_key == secret
        return {"ok": True, "message": "连接成功"}

    monkeypatch.setattr(models, "LlmModelConfig", FakeLlmModelConfig)
    monkeypatch.setattr(views, "check_ai_permission", lambda: None)
    monkeypatch.setattr(views, "test_connectivity", fake_test_connectivity)
    monkeypatch.setattr(views, "response_ok", lambda data: captured.setdefault("data", data))
    request = SimpleNamespace(
        json={"id": "cfg-1", "base_url": "https://x/v1", "model_name": "aioe"},
        ctx=SimpleNamespace(session={"tenant_id": "t1"}),
    )

    asyncio.run(inspect.unwrap(views.test_view)(request))

    assert secret not in json.dumps(captured["data"], ensure_ascii=False)


def test_tester_reports_success(monkeypatch):
    from services.llm_config import tester

    class FakeResponse:
        status = 200

        async def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

        async def text(self):
            return "ok"

    monkeypatch.setattr(tester, "_post_chat", lambda *a, **k: _async(FakeResponse()))
    result = asyncio.run(tester.test_connectivity("https://x/v1", "sk-1", "aioe"))
    assert result["ok"] is True


def test_tester_maps_401_to_readable_message(monkeypatch):
    from services.llm_config import tester

    class FakeResponse:
        status = 401

        async def json(self):
            return {"error": {"message": "invalid api key"}}

        async def text(self):
            return "invalid api key"

    monkeypatch.setattr(tester, "_post_chat", lambda *a, **k: _async(FakeResponse()))
    result = asyncio.run(tester.test_connectivity("https://x/v1", "sk-bad", "aioe"))
    assert result["ok"] is False
    assert "401" in result["message"] or "密钥" in result["message"]


def test_tester_result_never_contains_api_key(monkeypatch):
    from services.llm_config import tester

    class FakeResponse:
        status = 500

        async def json(self):
            raise ValueError("not json")

        async def text(self):
            return "boom"

    monkeypatch.setattr(tester, "_post_chat", lambda *a, **k: _async(FakeResponse()))
    result = asyncio.run(tester.test_connectivity("https://x/v1", "sk-super-secret", "aioe"))
    assert "sk-super-secret" not in str(result)


async def _async_impl(value):
    return value


def _async(value):
    return _async_impl(value)
