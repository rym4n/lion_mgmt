# coding=utf-8
import asyncio
import sys
import types
from types import SimpleNamespace

import pytest


def _load_resolver(records):
    """加载 resolver，把 LlmModelConfig 的查询替换为对 records 列表的内存过滤。

    records: list[SimpleNamespace]，每个含 id/name/base_url/api_key/model_name/
             purpose/enabled/deleted/tenant_id
    """
    for name in ("models", "core", "core.error_code", "libs", "libs.error_code",
                 "libs.error_code.exception", "services", "services.llm_config",
                 "services.llm_config.resolver"):
        sys.modules.pop(name, None)

    class FakeQuerySet:
        def __init__(self, items):
            self._items = items

        async def first(self):
            return self._items[0] if self._items else None

    class FakeLlmModelConfig:
        @staticmethod
        def filter(**kwargs):
            items = []
            for r in records:
                if all(getattr(r, k, None) == v for k, v in kwargs.items()):
                    items.append(r)
            return FakeQuerySet(items)

    models_module = types.ModuleType("models")
    models_module.LlmModelConfig = FakeLlmModelConfig
    sys.modules["models"] = models_module

    import importlib
    import services.llm_config.resolver as resolver_module
    return importlib.reload(resolver_module)


def _record(**kwargs):
    base = {
        "id": "cfg-1",
        "name": "宏云-aioe",
        "base_url": "https://oapi.example.com/v1",
        "api_key": "sk-plain",
        "model_name": "aioe",
        "purpose": "chat",
        "enabled": True,
        "deleted": False,
        "tenant_id": "t1",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_resolves_by_purpose_chat_as_default():
    resolver = _load_resolver([_record()])
    result = asyncio.run(resolver.resolve_llm("t1"))
    assert result.base_url == "https://oapi.example.com/v1"
    assert result.api_key == "sk-plain"
    assert result.model_name == "aioe"
    assert result.config_id == "cfg-1"


def test_resolves_lite_purpose_separately():
    resolver = _load_resolver([
        _record(id="cfg-chat", purpose="chat", model_name="aioe"),
        _record(id="cfg-lite", purpose="lite", model_name="aioe-lite"),
    ])
    result = asyncio.run(resolver.resolve_llm("t1", purpose="lite"))
    assert result.model_name == "aioe-lite"
    assert result.config_id == "cfg-lite"


def test_explicit_config_id_wins_over_purpose():
    resolver = _load_resolver([
        _record(id="cfg-chat", purpose="chat", model_name="aioe"),
        _record(id="cfg-x", purpose=None, model_name="gpt-4o", name="私有-gpt4o"),
    ])
    result = asyncio.run(resolver.resolve_llm("t1", config_id="cfg-x"))
    assert result.model_name == "gpt-4o"


def test_falls_back_to_default_when_bound_config_soft_deleted():
    """SPEC §4.3 软删除陷阱的回归测试。

    软删除是 update(deleted=True)，物理行不消失，on_delete=SET_NULL 不触发，
    员工外键会继续指向已删记录。此时必须回落全局默认，绝不能抛错——
    否则删掉一条被绑定的配置就会让该员工彻底不可用。
    """
    resolver = _load_resolver([
        _record(id="cfg-chat", purpose="chat", model_name="aioe"),
        _record(id="cfg-gone", purpose=None, deleted=True, model_name="gone-model"),
    ])
    result = asyncio.run(resolver.resolve_llm("t1", config_id="cfg-gone"))
    assert result.model_name == "aioe", "应回落到全局默认而非抛错"
    assert result.config_id == "cfg-chat"


def test_falls_back_to_default_when_bound_config_disabled():
    resolver = _load_resolver([
        _record(id="cfg-chat", purpose="chat", model_name="aioe"),
        _record(id="cfg-off", purpose=None, enabled=False, model_name="off-model"),
    ])
    result = asyncio.run(resolver.resolve_llm("t1", config_id="cfg-off"))
    assert result.model_name == "aioe"


def test_falls_back_when_config_id_is_stale_garbage():
    resolver = _load_resolver([_record(id="cfg-chat", purpose="chat")])
    result = asyncio.run(resolver.resolve_llm("t1", config_id="does-not-exist"))
    assert result.config_id == "cfg-chat"


def test_raises_when_nothing_configured():
    """SPEC §3.5 破坏性升级：未配置就报错，且必须是可理解的业务错误。"""
    resolver = _load_resolver([])
    from libs.error_code.exception import CustomError

    with pytest.raises(CustomError) as exc:
        asyncio.run(resolver.resolve_llm("t1"))
    assert exc.value.enum.code == 4180


def test_raises_when_default_also_disabled():
    resolver = _load_resolver([_record(purpose="chat", enabled=False)])
    from libs.error_code.exception import CustomError

    with pytest.raises(CustomError):
        asyncio.run(resolver.resolve_llm("t1"))


def test_tenant_isolation():
    """其他租户的配置不可见。"""
    resolver = _load_resolver([_record(tenant_id="t-other", purpose="chat")])
    from libs.error_code.exception import CustomError

    with pytest.raises(CustomError):
        asyncio.run(resolver.resolve_llm("t1"))


def test_to_payload_matches_lion_agent_contract():
    """载荷结构必须与 lion_agent 的 ExecuteRequest.llm 一致（Task 1）。"""
    resolver = _load_resolver([_record()])
    result = asyncio.run(resolver.resolve_llm("t1"))
    assert result.to_payload() == {
        "base_url": "https://oapi.example.com/v1",
        "api_key": "sk-plain",
        "model": "aioe",
    }


def test_repr_does_not_leak_api_key():
    """凭证不得出现在 repr/日志中。"""
    resolver = _load_resolver([_record(api_key="sk-super-secret-value")])
    result = asyncio.run(resolver.resolve_llm("t1"))
    assert "sk-super-secret-value" not in repr(result)
