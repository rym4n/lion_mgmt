# coding=utf-8
"""知识库 embedding 配置来源与指纹（SPEC §7.1 D9）。

核心契约：配置来自既有的 llm_model_config（purpose='embedding'），
不是另起一张表。这条被破坏过一次——用户在模型配置页配好了 embedding，
知识空间页却报「服务不可用」，因为读的是另一张空表。
"""

import pytest

from models.tenant_embedding import compute_fingerprint


def test_fingerprint_changes_with_dimensions():
    assert compute_fingerprint("http://e/v1", "m", 1024) != compute_fingerprint(
        "http://e/v1", "m", 2048
    )


def test_fingerprint_ignores_trailing_slash():
    assert compute_fingerprint("http://e/v1/", "m", 1024) == compute_fingerprint(
        "http://e/v1", "m", 1024
    )


def test_fingerprint_length_matches_lion_agent():
    """必须与 lion_agent 的 embedding_fingerprint 同算法同长度，否则对不上账。"""
    assert len(compute_fingerprint("http://e/v1", "m", 1024)) == 16


def test_config_source_is_llm_model_config():
    """配置必须读既有的 llm_model_config，不得再建一张配置表。"""
    import inspect

    from models import tenant_embedding

    src = inspect.getsource(tenant_embedding)
    assert "LlmModelConfig" in src, "应从 llm_model_config 读取 embedding 配置"
    assert "class TenantEmbeddingConfig" not in src, (
        "不得重建配置表——llm_model_config 已有 purpose/dimensions/api_key"
    )


def test_index_state_table_holds_no_config_fields():
    """记账表只记指纹，不得复制 base_url/model/api_key 等配置字段。"""
    from models.tenant_embedding import TenantEmbeddingIndexState

    fields = set(TenantEmbeddingIndexState._meta.fields_map)
    for leaked in ("base_url", "model", "api_key", "dimensions"):
        assert leaked not in fields, f"{leaked} 属于配置，不该出现在记账表"
    assert "indexed_fingerprint" in fields


@pytest.mark.asyncio
async def test_payload_none_when_dimensions_missing(monkeypatch):
    """维度缺失一律视为未配置——猜错会把整个 collection 建错。"""
    from models import tenant_embedding

    class _Cfg:
        base_url = "http://e/v1"
        model_name = "m"
        dimensions = None
        api_key = "k"

    async def fake(tenant_id):
        return _Cfg()

    monkeypatch.setattr(tenant_embedding, "get_embedding_config", fake)
    assert await tenant_embedding.get_embedding_payload("t1") is None


@pytest.mark.asyncio
async def test_payload_maps_model_name_to_model(monkeypatch):
    """llm_model_config 的字段是 model_name，下发给 lion_agent 的键是 model。"""
    from models import tenant_embedding

    class _Cfg:
        base_url = "http://e/v1"
        model_name = "qwen3-embedding-4b"
        dimensions = 1024
        api_key = "secret"

    async def fake(tenant_id):
        return _Cfg()

    monkeypatch.setattr(tenant_embedding, "get_embedding_config", fake)
    payload = await tenant_embedding.get_embedding_payload("t1")
    assert payload == {
        "base_url": "http://e/v1",
        "model": "qwen3-embedding-4b",
        "dimensions": 1024,
        "api_key": "secret",
    }


@pytest.mark.asyncio
async def test_status_first_index_is_not_rebuild_required(monkeypatch):
    """从未建过索引不算「待重建」——那是首次使用，不该提示用户重建。"""
    from models import tenant_embedding

    class _Cfg:
        name = "emb"
        base_url = "http://e/v1"
        model_name = "m"
        dimensions = 1024

    async def fake_cfg(tenant_id):
        return _Cfg()

    async def fake_state(**kwargs):
        return None

    monkeypatch.setattr(tenant_embedding, "get_embedding_config", fake_cfg)
    monkeypatch.setattr(
        tenant_embedding.TenantEmbeddingIndexState, "get_or_none", fake_state
    )
    status = await tenant_embedding.get_embedding_status("t1")
    assert status["configured"] is True
    assert status["rebuild_required"] is False
