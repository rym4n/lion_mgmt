# coding=utf-8
"""lion_agent 入库回调的状态落库（SPEC §3.5 D5）。"""

import pytest

from libs.error_code.exception import CustomError


class _FakeDoc:
    def __init__(self, tenant_id="t1", doc_id="doc-1"):
        self.id = doc_id
        self.tenant_id = tenant_id
        self.status = "processing"
        self.error = None
        self.indexed_at = None
        self.saved = False

    async def save(self):
        self.saved = True

    @property
    def json(self):
        return {"id": self.id, "status": self.status, "error": self.error}


def _patch_lookup(monkeypatch, document):
    """把 KnowledgeDocument.get_or_none 换成只认对 (id, tenant) 的假查询。"""
    from services.knowledge import callbacks

    async def fake_get_or_none(**kwargs):
        if document is None:
            return None
        if str(kwargs.get("id")) != str(document.id):
            return None
        if kwargs.get("tenant_id") != document.tenant_id:
            return None
        return document

    monkeypatch.setattr(callbacks.KnowledgeDocument, "get_or_none", fake_get_or_none)


@pytest.mark.asyncio
async def test_ready_sets_indexed_at(monkeypatch):
    from services.knowledge.callbacks import apply_ingest_result

    document = _FakeDoc()
    _patch_lookup(monkeypatch, document)

    result = await apply_ingest_result("t1", "doc-1", {"status": "ready", "chunk_count": 3})
    assert result["status"] == "ready"
    assert document.status == "ready"
    assert document.indexed_at is not None
    assert document.saved is True


@pytest.mark.asyncio
async def test_failed_records_truncated_error(monkeypatch):
    from services.knowledge.callbacks import apply_ingest_result

    document = _FakeDoc()
    _patch_lookup(monkeypatch, document)

    await apply_ingest_result("t1", "doc-1", {"status": "failed", "error": "x" * 3000})
    assert document.status == "failed"
    assert len(document.error) <= 1000


@pytest.mark.asyncio
async def test_unknown_status_is_rejected(monkeypatch):
    from services.knowledge.callbacks import apply_ingest_result

    _patch_lookup(monkeypatch, _FakeDoc())
    with pytest.raises(CustomError):
        await apply_ingest_result("t1", "doc-1", {"status": "whatever"})


@pytest.mark.asyncio
async def test_callback_is_tenant_scoped(monkeypatch):
    """跨租户的回调必须打不中——document_id 可枚举，隔离不能只靠它。"""
    from services.knowledge.callbacks import apply_ingest_result

    _patch_lookup(monkeypatch, _FakeDoc(tenant_id="t1"))
    with pytest.raises(CustomError):
        await apply_ingest_result("another-tenant", "doc-1", {"status": "ready"})


@pytest.mark.asyncio
async def test_late_callback_does_not_restore_invalidated_index(monkeypatch):
    from services.knowledge.callbacks import apply_ingest_result

    document = _FakeDoc()
    document.status = "failed"
    document.error = "索引已重建，请重试文档"
    _patch_lookup(monkeypatch, document)

    result = await apply_ingest_result("t1", "doc-1", {"status": "ready"})

    assert result["status"] == "failed"
    assert document.indexed_at is None
    assert document.saved is False
