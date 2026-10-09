# coding=utf-8
"""A row action rebuilds only the selected space's documents."""

import inspect
from types import SimpleNamespace

import pytest


class _Doc:
    def __init__(self, doc_id, space_key):
        self.id = doc_id
        self.space_key = space_key
        self.status = "ready"
        self.error = None
        self.indexed_at = "yesterday"
        self.ingest_attempt_id = "old"


class _Query:
    def __init__(self, documents, calls):
        self.documents = documents
        self.calls = calls

    async def all(self):
        return self.documents

    async def update(self, **values):
        self.calls.append(("invalidate", values))
        for doc in self.documents:
            for name, value in values.items():
                setattr(doc, name, value)
        return len(self.documents)


def _request(space_key="ops"):
    return SimpleNamespace(
        json={"space_key": space_key},
        ctx=SimpleNamespace(
            session={"tenant_id": "t1", "account_info": {"id": "admin"}}
        ),
    )


def _patch_rebuild(monkeypatch, documents, *, fail_rebuild=False, fail_doc=None):
    from services.knowledge import embedding_views

    calls = []

    async def get_space(tenant_id, space_key):
        calls.append(("space", tenant_id, space_key))
        return SimpleNamespace(space_key=space_key)

    async def payload(tenant_id):
        return {"base_url": "http://e", "model": "m", "dimensions": 2048}

    class _Client:
        async def rebuild_space(self, **kwargs):
            calls.append(("rebuild_space", kwargs["tenant_id"], kwargs["space_key"], kwargs["document_ids"]))
            if fail_rebuild:
                raise RuntimeError("upstream unavailable")
            return {"status": "cleared", "deleted_count": len(kwargs["document_ids"])}

    async def state(**kwargs):
        class _State:
            indexed_fingerprint = "new"
            rebuilt_at = None
            rebuilt_by = None

            async def save(self):
                calls.append(("state", self.indexed_fingerprint))

        return _State()

    async def submit(tenant_id, document, embedding, **kwargs):
        calls.append(("submit", document.id))
        if document.id == fail_doc:
            raise RuntimeError("submit failed")
        document.status = "processing"

    def filter_documents(**kwargs):
        assert kwargs == {"tenant_id": "t1", "space_key": "ops", "deleted": False}
        return _Query([doc for doc in documents if doc.space_key == "ops"], calls)

    monkeypatch.setattr(embedding_views.manager, "get_space_or_404", get_space)
    monkeypatch.setattr(embedding_views.manager, "submit_document_ingest", submit)
    monkeypatch.setattr(embedding_views, "get_embedding_payload", payload)
    monkeypatch.setattr(embedding_views, "compute_fingerprint", lambda *args: "new")
    monkeypatch.setattr(embedding_views, "get_knowledge_client", lambda: _Client())
    monkeypatch.setattr(embedding_views.KnowledgeDocument, "filter", filter_documents)
    monkeypatch.setattr(embedding_views.TenantEmbeddingIndexState, "get_or_none", state)
    monkeypatch.setattr(embedding_views, "response_ok", lambda data: data)
    return embedding_views, calls


@pytest.mark.asyncio
async def test_rebuild_queues_only_selected_space(monkeypatch):
    documents = [_Doc("doc-ops", "ops"), _Doc("doc-sec", "sec")]
    views, calls = _patch_rebuild(monkeypatch, documents)

    result = await inspect.unwrap(views.rebuild_index)(_request())

    assert result["document_count"] == 1
    assert result["space_key"] == "ops"
    assert result["submitted_count"] == 1
    assert result["failed_count"] == 0
    assert [call[0] for call in calls] == [
        "space", "rebuild_space", "invalidate", "state", "submit"
    ]
    assert calls[1][3] == ["doc-ops"]
    assert calls[2][1]["ingest_attempt_id"] is None
    assert documents[0].status == "processing"
    assert documents[1].status == "ready"


@pytest.mark.asyncio
async def test_rebuild_failure_keeps_documents_unchanged(monkeypatch):
    documents = [_Doc("doc-ops", "ops")]
    views, calls = _patch_rebuild(monkeypatch, documents, fail_rebuild=True)

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        await inspect.unwrap(views.rebuild_index)(_request())

    assert [call[0] for call in calls] == ["space", "rebuild_space"]
    assert documents[0].status == "ready"
    assert documents[0].ingest_attempt_id == "old"


@pytest.mark.asyncio
async def test_partial_submission_failure_is_reported(monkeypatch):
    documents = [_Doc("doc-ops", "ops"), _Doc("doc-sec", "sec")]
    views, _ = _patch_rebuild(monkeypatch, documents, fail_doc="doc-ops")

    result = await inspect.unwrap(views.rebuild_index)(_request())

    assert result["submitted_count"] == 0
    assert result["failed_count"] == 1


@pytest.mark.asyncio
async def test_rebuild_rejects_model_change_for_shared_collection(monkeypatch):
    from libs.error_code.exception import CustomError
    from services.knowledge import embedding_views

    documents = [_Doc("doc-ops", "ops")]
    views, calls = _patch_rebuild(monkeypatch, documents)

    async def old_state(**kwargs):
        class _State:
            indexed_fingerprint = "old"

        return _State()

    monkeypatch.setattr(embedding_views.TenantEmbeddingIndexState, "get_or_none", old_state)
    with pytest.raises(CustomError):
        await inspect.unwrap(views.rebuild_index)(_request())
    assert [call[0] for call in calls] == ["space"]


@pytest.mark.asyncio
async def test_rebuild_requires_space_key(monkeypatch):
    from libs.error_code.exception import CustomError
    from services.knowledge import embedding_views

    with pytest.raises(CustomError):
        await inspect.unwrap(embedding_views.rebuild_index)(_request(""))
