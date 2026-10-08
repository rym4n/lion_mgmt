# coding=utf-8
"""Explicit index rebuild invalidates only the current tenant's indexed documents."""

from types import SimpleNamespace
import inspect

import pytest


class _Query:
    def __init__(self, calls):
        self.calls = calls

    async def update(self, **values):
        self.calls.append(("update", values))
        return 3


@pytest.mark.asyncio
async def test_rebuild_invalidates_documents_after_collection_drop(monkeypatch):
    from services.knowledge import embedding_views

    calls = []

    class _Client:
        async def rebuild(self, **kwargs):
            calls.append(("rebuild", kwargs))
            return {"status": "dropped"}

    class _State:
        indexed_fingerprint = "old"
        rebuilt_at = None
        rebuilt_by = None

        async def save(self):
            calls.append(("save", self.indexed_fingerprint))

    async def payload(tenant_id):
        assert tenant_id == "t1"
        return {"base_url": "http://e", "model": "m", "dimensions": 2048}

    async def state(**kwargs):
        assert kwargs == {"tenant_id": "t1", "deleted": False}
        return _State()

    def filter_documents(**kwargs):
        calls.append(("filter", kwargs))
        return _Query(calls)

    monkeypatch.setattr(embedding_views, "get_embedding_payload", payload)
    monkeypatch.setattr(
        embedding_views, "compute_fingerprint", lambda *args: "new",
    )
    monkeypatch.setattr(embedding_views, "get_knowledge_client", lambda: _Client())
    monkeypatch.setattr(embedding_views.TenantEmbeddingIndexState, "get_or_none", state)
    monkeypatch.setattr(embedding_views.KnowledgeDocument, "filter", filter_documents)
    monkeypatch.setattr(embedding_views, "response_ok", lambda data: data)
    request = SimpleNamespace(ctx=SimpleNamespace(session={"tenant_id": "t1", "account_info": {"id": "admin"}}))

    result = await inspect.unwrap(embedding_views.rebuild_index)(request)

    assert result["status"] == "dropped"
    assert result["retry_required_count"] == 3
    assert [call[0] for call in calls] == ["rebuild", "filter", "update", "save"]
    assert calls[1][1] == {
        "tenant_id": "t1", "deleted": False, "status__in": ["ready", "processing"]
    }
    assert calls[2][1] == {
        "status": "failed", "error": "索引已重建，请重试文档", "indexed_at": None
    }


@pytest.mark.asyncio
async def test_rebuild_failure_does_not_invalidate_documents(monkeypatch):
    from services.knowledge import embedding_views

    class _Client:
        async def rebuild(self, **kwargs):
            raise RuntimeError("collection unavailable")

    async def payload(tenant_id):
        return {"base_url": "http://e", "model": "m", "dimensions": 2048}

    monkeypatch.setattr(embedding_views, "get_embedding_payload", payload)
    monkeypatch.setattr(embedding_views, "get_knowledge_client", lambda: _Client())
    monkeypatch.setattr(
        embedding_views.KnowledgeDocument, "filter",
        lambda **kwargs: pytest.fail("documents changed before collection drop"),
    )
    request = SimpleNamespace(ctx=SimpleNamespace(session={"tenant_id": "t1"}))

    with pytest.raises(RuntimeError):
        await inspect.unwrap(embedding_views.rebuild_index)(request)
