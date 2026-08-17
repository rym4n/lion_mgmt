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


DEFAULT_TENANT_ID = "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18"


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
        # 模型配置已收敛到 default 租户（SPEC §5.6）：resolver 固定按这个
        # tenant_id 查，所以 fixture 的默认值必须与之一致，否则永远查不到。
        "tenant_id": DEFAULT_TENANT_ID,
        "dimensions": None,
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


def test_non_default_tenant_config_is_invisible():
    """模型配置收敛到 default 租户后，非 default 租户的存量记录不可见。

    这不是「租户隔离」——收敛后隔离已不作用于这张表（见下一条）。
    它锁的是：迁移前遗留在其它租户下的脏数据不会被解析到。
    """
    resolver = _load_resolver([_record(tenant_id="t-other", purpose="chat")])
    from libs.error_code.exception import CustomError

    with pytest.raises(CustomError):
        asyncio.run(resolver.resolve_llm("t1"))


def test_any_tenant_session_resolves_the_same_default_pool():
    """任意租户会话都解析到同一份 default 配置（SPEC §5.6 的收敛语义）。

    tenant_id 参数保留但不参与查询——这条测试锁死这个行为，防止
    有人「看它没被用到」就把参数删掉、或把租户过滤加回来。
    """
    resolver = _load_resolver([_record()])
    for caller_tenant in ("t1", "t-other", DEFAULT_TENANT_ID):
        result = asyncio.run(resolver.resolve_llm(caller_tenant))
        assert result.config_id == "cfg-1"


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


def test_embedding_purpose_carries_dimensions():
    """SPEC §5.6：embedding 配置必须把维度带到 ResolvedLlm。"""
    resolver = _load_resolver([
        _record(id="cfg-emb", purpose="embedding",
                model_name="text-embedding-v4", dimensions=1024),
    ])
    result = asyncio.run(resolver.resolve_llm("t1", purpose="embedding"))
    assert result.dimensions == 1024
    assert result.model_name == "text-embedding-v4"


def test_chat_config_resolves_dimensions_as_none():
    """chat 配置不填维度，解析结果必须是 None 而不是 0。
    0 会被 OpenAIEmbeddingModel 当成合法维度传下去。"""
    resolver = _load_resolver([_record(purpose="chat", dimensions=None)])
    result = asyncio.run(resolver.resolve_llm("t1"))
    assert result.dimensions is None


def test_embedding_payload_uses_model_key_not_model_name():
    """SPEC §5.6 明确要求两侧键名统一为 model。
    写成 model_name 会让 lion_agent 侧要处理两套键名。"""
    resolver = _load_resolver([
        _record(id="cfg-emb", purpose="embedding",
                model_name="text-embedding-v4", dimensions=1024),
    ])
    result = asyncio.run(resolver.resolve_llm("t1", purpose="embedding"))
    payload = result.to_payload()
    assert payload["model"] == "text-embedding-v4"
    assert "model_name" not in payload
    assert payload["dimensions"] == 1024


def test_chat_payload_shape_is_unchanged_by_dimensions_field():
    """回归：给 ResolvedLlm 加字段不得改变 chat/lite 的既有载荷形状。
    无条件输出 dimensions=None 会给所有 payload['llm'] 塞一个恒空键。"""
    resolver = _load_resolver([_record(purpose="chat")])
    result = asyncio.run(resolver.resolve_llm("t1"))
    assert result.to_payload() == {
        "base_url": "https://oapi.example.com/v1",
        "api_key": "sk-plain",
        "model": "aioe",
    }


def test_embedding_without_dimensions_yields_three_key_payload():
    """⚠️ P5 侧报的冲突场景：**租户配了 embedding 但没填 dimensions**。

    这是**合法状态**——`dimensions` 是可空列，管理员可以只填端点与模型名。
    此时 payload 必须是三键（不含 `dimensions`），而不是四键含 null。
    P5 的断言已改为同时接受三键/四键两种形状，两侧靠这条测试对齐。

    D45 的形状定义是「`dimensions is None` 时该键**不出现**」，
    与「输出 `"dimensions": null`」是两种不兼容的形状，不可混用。
    """
    resolver = _load_resolver([
        _record(id="cfg-emb", purpose="embedding",
                model_name="text-embedding-v4", dimensions=None),
    ])
    result = asyncio.run(resolver.resolve_llm("t1", purpose="embedding"))

    payload = result.to_payload()
    assert "dimensions" not in payload, \
        "D45：dimensions 为 None 时该键必须不出现，而不是输出 null"
    assert payload == {
        "base_url": "https://oapi.example.com/v1",
        "api_key": "sk-plain",
        "model": "text-embedding-v4",
    }


def test_payload_never_contains_explicit_null_dimensions():
    """把 D45 的形状约定钉成一条独立断言：任何 purpose、任何取值下，
    payload 里都不允许出现值为 None 的 dimensions 键。

    单独写一条是因为上面两条各自只覆盖一种 purpose，
    而这条约束是全局的——它是 P6↔P5 唯一的形状耦合点。
    """
    for purpose, dims in (("chat", None), ("lite", None), ("embedding", None)):
        resolver = _load_resolver([_record(id=f"cfg-{purpose}", purpose=purpose,
                                           dimensions=dims)])
        result = asyncio.run(resolver.resolve_llm("t1", purpose=purpose))
        payload = result.to_payload()
        assert payload.get("dimensions", "ABSENT") == "ABSENT", \
            f"purpose={purpose} 的 payload 出现了 dimensions 键"



def test_repr_still_hides_api_key_after_adding_dimensions():
    """契约 §4.4：新增字段不得破坏 api_key 的 repr=False。
    把 dimensions 声明在 api_key 之前会迫使它也带默认值，
    重排字段顺序时最容易顺手把 repr=False 弄掉。"""
    resolver = _load_resolver([
        _record(api_key="sk-super-secret-value", purpose="embedding", dimensions=768),
    ])
    result = asyncio.run(resolver.resolve_llm("t1", purpose="embedding"))
    rendered = repr(result)
    assert "sk-super-secret-value" not in rendered
    assert "768" in rendered, "dimensions 不是凭证，应当可见以便排障"

