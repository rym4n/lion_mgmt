# coding=utf-8
"""Agent 面隔离行为（SPEC §3.1、§3.3）。"""

import inspect
from pathlib import Path

import pytest

from libs.error_code.exception import CustomError


class _FakeEmployee:
    def __init__(self, **kw):
        self.id = kw.get("id", "emp-1")
        self.tenant_id = kw.get("tenant_id", "t1")
        self.knowledge_space_keys = kw.get("space_keys", ["ops"])
        self.knowledge_write_space = kw.get("write_space")
        self.deleted = False


@pytest.mark.asyncio
async def test_search_limits_space_keys_to_visible_spaces(monkeypatch):
    from services.knowledge import agent_views

    captured = {}

    async def fake_scope(tenant_id, space_keys, requested):
        captured["keys"] = list(space_keys)
        return ["ops", "runbook"]

    class _Client:
        async def search(self, **kwargs):
            captured["search"] = kwargs
            return [{"document_id": "d1", "excerpt": "x"}]

    async def fake_embedding(tenant_id):
        return {"base_url": "http://e", "model": "m", "dimensions": 8}

    async def fake_decorate(tenant_id, hits):
        return hits

    monkeypatch.setattr(agent_views.scope, "resolve_search_scope", fake_scope)
    monkeypatch.setattr(agent_views, "get_embedding_payload", fake_embedding)
    monkeypatch.setattr(agent_views, "get_knowledge_client", lambda: _Client())
    monkeypatch.setattr(agent_views, "_decorate_hits", fake_decorate)
    result = await agent_views.do_search(_FakeEmployee(space_keys=["ops", "runbook"]), "q")
    assert captured["keys"] == ["ops", "runbook"]
    assert captured["search"]["space_keys"] == ["ops", "runbook"]
    assert result[0]["document_id"] == "d1"


@pytest.mark.asyncio
async def test_search_with_unbound_space_returns_empty(monkeypatch):
    from services.knowledge import agent_views

    called = {"search": False}

    async def fake_scope(tenant_id, space_keys, requested):
        return []

    class _Client:
        async def search(self, **kwargs):
            called["search"] = True
            return []

    monkeypatch.setattr(agent_views.scope, "resolve_search_scope", fake_scope)
    monkeypatch.setattr(agent_views, "get_knowledge_client", lambda: _Client())
    assert await agent_views.do_search(_FakeEmployee(space_keys=["ops"]), "x", space_key="secret") == []
    assert called["search"] is False


@pytest.mark.asyncio
async def test_search_with_no_embedding_returns_empty(monkeypatch):
    from services.knowledge import agent_views

    async def fake_scope(tenant_id, space_keys, requested):
        return ["ops"]

    async def no_embedding(tenant_id):
        return None

    monkeypatch.setattr(agent_views.scope, "resolve_search_scope", fake_scope)
    monkeypatch.setattr(agent_views, "get_embedding_payload", no_embedding)
    assert await agent_views.do_search(_FakeEmployee(), "x") == []


@pytest.mark.asyncio
async def test_read_rejects_document_outside_visible_spaces(monkeypatch):
    from core.error_code import ErrorCode
    from services.knowledge import agent_views

    class _Doc:
        id = "doc-9"
        space_key = "secret"
        title = "机密"

    async def fake_get_doc(tenant_id, document_id):
        return _Doc()

    monkeypatch.setattr(agent_views, "get_document_or_404", fake_get_doc)
    with pytest.raises(CustomError) as excinfo:
        await agent_views.do_read(_FakeEmployee(space_keys=["ops"]), "doc-9")
    assert excinfo.value.enum is ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND


@pytest.mark.asyncio
async def test_read_gets_text_from_rustfs(monkeypatch):
    from services.knowledge import agent_views

    class _Doc:
        id = "doc-1"
        space_key = "ops"
        title = "runbook"

    async def fake_get_doc(tenant_id, document_id):
        return _Doc()

    async def fake_text(document_id):
        assert document_id == "doc-1"
        return "全文"

    monkeypatch.setattr(agent_views, "get_document_or_404", fake_get_doc)
    monkeypatch.setattr(agent_views.object_store, "get_text", fake_text)
    result = await agent_views.do_read(_FakeEmployee(), "doc-1")
    assert result["content"] == "全文"


@pytest.mark.asyncio
async def test_write_without_write_space_is_rejected():
    from core.error_code import ErrorCode
    from services.knowledge import agent_views

    with pytest.raises(CustomError) as excinfo:
        await agent_views.do_write(_FakeEmployee(space_keys=["ops"], write_space=None), title="t", content="c")
    assert excinfo.value.enum is ErrorCode.KNOWLEDGE_WRITE_NOT_ALLOWED


@pytest.mark.asyncio
async def test_write_goes_to_configured_space(monkeypatch):
    from services.knowledge import agent_views

    captured = {}

    async def fake_upsert(tenant_id, **kwargs):
        captured.update(kwargs)
        captured["tenant_id"] = tenant_id
        return {"id": "doc-new", "status": "processing"}

    monkeypatch.setattr(agent_views, "upsert_document", fake_upsert)
    await agent_views.do_write(
        _FakeEmployee(space_keys=["ops", "runbook"], write_space="runbook"),
        title="处置记录", content="内容",
    )
    assert captured["space_key"] == "runbook"
    assert captured["source"] == "agent"
    assert captured["source_ref"] == "emp-1"
    assert captured["filename"].endswith(".md")


def test_write_signature_has_no_path():
    from services.knowledge import agent_views

    sig = inspect.signature(agent_views.do_write)
    assert "path" not in sig.parameters
    assert "space" not in sig.parameters
    assert "space_key" not in sig.parameters


def test_agent_blueprint_prefix():
    src = (Path(__file__).resolve().parents[2] / "services" / "knowledge" / "agent_views.py").read_text(encoding="utf-8")
    assert 'url_prefix="/knowledge/agent"' in src


def test_agent_views_never_reads_tenant_from_body():
    src = (Path(__file__).resolve().parents[2] / "services" / "knowledge" / "agent_views.py").read_text(encoding="utf-8")
    assert 'data.get("tenant_id")' not in src
    assert 'params.get("tenant_id")' not in src
