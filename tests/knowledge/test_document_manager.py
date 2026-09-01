# coding=utf-8
"""文档上传、幂等、删除与配额（SPEC §3.4、§3.5）。"""

import hashlib
from datetime import datetime, timedelta

import pytest

from libs.error_code.exception import CustomError


class _FakeSpace:
    def __init__(self, space_key="ops", tenant_id="t1"):
        self.id = "space-1"
        self.space_key = space_key
        self.tenant_id = tenant_id
        self.name = "运维"
        self.deleted = False


class _FakeDoc:
    def __init__(self, **kw):
        self.id = kw.get("id", "doc-1")
        self.tenant_id = kw.get("tenant_id", "t1")
        self.space_key = kw.get("space_key", "ops")
        self.title = kw.get("title", "a.md")
        self.content_hash = kw.get("content_hash", "")
        self.size = kw.get("size", 0)
        self.status = kw.get("status", "processing")
        self.storage_key = kw.get("storage_key", "knowledge/doc-1/raw")
        self.error = kw.get("error")
        self.indexed_at = kw.get("indexed_at")
        self.source = kw.get("source", "manual")
        self.uploader_id = kw.get("uploader_id")
        self.deleted = False
        self.saved = False
        from settings import TZ

        self.utime = kw.get("utime", datetime.now(TZ))

    async def save(self):
        self.saved = True

    @property
    def json(self):
        return {"id": self.id, "status": self.status, "title": self.title}


class _FakeClient:
    def __init__(self):
        self.ingests = []
        self.deleted = []

    async def ingest(self, **kwargs):
        self.ingests.append(kwargs)
        return {"status": "accepted"}

    async def delete_document(self, **kwargs):
        self.deleted.append(kwargs)
        return {}


class _FakeQuery:
    def __init__(self, docs=None, count=0):
        self.docs = docs or []
        self._count = count

    async def all(self):
        return self.docs

    async def count(self):
        return self._count if self._count else len(self.docs)

    def filter(self, **kwargs):
        return self

    def offset(self, value):
        return self

    def limit(self, value):
        return self

    async def update(self, **kwargs):
        return self._count


def _patch_common(monkeypatch, *, space=None, client=None, existing=None, embedding=None):
    from services.knowledge import manager

    async def fake_get_space(tenant_id, space_key):
        if space is None:
            raise CustomError(__import__("core.error_code", fromlist=["ErrorCode"]).ErrorCode.KNOWLEDGE_SPACE_NOT_FOUND)
        return space

    def fake_get_client():
        return client

    async def fake_embedding(tenant_id):
        return embedding if embedding is not None else {
            "base_url": "http://e", "model": "m", "dimensions": 8
        }

    async def fake_doc_get_or_none(**kwargs):
        return existing

    monkeypatch.setattr(manager, "get_space_or_404", fake_get_space)
    monkeypatch.setattr(manager, "get_knowledge_client", fake_get_client)
    monkeypatch.setattr(manager, "get_embedding_payload", fake_embedding)
    monkeypatch.setattr(manager.KnowledgeDocument, "get_or_none", fake_doc_get_or_none)
    monkeypatch.setattr(manager.KnowledgeDocument, "filter", lambda **kwargs: _FakeQuery())
    monkeypatch.setattr(manager.object_store, "put_raw", lambda doc_id, data: _put_raw(doc_id))
    monkeypatch.setattr(manager.object_store, "delete_objects", lambda doc_id: _delete_objects(doc_id))
    return manager


async def _put_raw(document_id):
    return f"knowledge/{document_id}/raw"


async def _delete_objects(document_id):
    return None


@pytest.mark.asyncio
async def test_upsert_rejects_disallowed_suffix(monkeypatch):
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=_FakeClient())
    with pytest.raises(CustomError):
        await manager.upsert_document("t1", space_key="ops", filename="evil.py", content=b"x")


@pytest.mark.asyncio
async def test_upsert_rejects_oversized_file(monkeypatch):
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=_FakeClient())
    with pytest.raises(CustomError):
        await manager.upsert_document(
            "t1", space_key="ops", filename="big.md", content=b"x" * (manager.MAX_FILE_SIZE + 1)
        )


@pytest.mark.asyncio
async def test_upsert_rejects_when_embedding_unconfigured(monkeypatch):
    """未配置 embedding 必须拒绝入库，不能用默认维度糊过去。"""
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=_FakeClient(), embedding=None)

    async def no_config(tenant_id):
        return None

    monkeypatch.setattr(manager, "get_embedding_payload", no_config)
    with pytest.raises(CustomError):
        await manager.upsert_document("t1", space_key="ops", content=b"x", filename="a.md")


@pytest.mark.asyncio
async def test_upsert_same_hash_skips_reupload(monkeypatch):
    content = b"# runbook"
    digest = hashlib.sha256(content).hexdigest()
    existing = _FakeDoc(content_hash=digest, status="ready")
    client = _FakeClient()
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=client, existing=existing)
    result = await manager.upsert_document("t1", space_key="ops", filename="a.md", content=content)
    assert client.ingests == []
    assert result["status"] == "ready"
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_upsert_creates_document_and_submits_ingest(monkeypatch):
    created = {}
    client = _FakeClient()
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=client)

    async def fake_create(**kwargs):
        created.update(kwargs)
        return _FakeDoc(**{k: v for k, v in kwargs.items() if k != "space"})

    monkeypatch.setattr(manager.KnowledgeDocument, "create", fake_create)
    await manager.upsert_document("t1", space_key="ops", filename="new.md", content=b"# new", source="api")
    assert created["space_key"] == "ops"
    assert created["source"] == "api"
    assert created["status"] == "processing"
    assert len(client.ingests) == 1
    assert client.ingests[0]["storage_key"] == "knowledge/doc-1/raw"


@pytest.mark.asyncio
async def test_reupload_records_latest_operator(monkeypatch):
    content = b"# repaired"
    existing = _FakeDoc(
        content_hash=hashlib.sha256(content).hexdigest(), status="failed"
    )
    manager = _patch_common(
        monkeypatch, space=_FakeSpace(), client=_FakeClient(), existing=existing
    )

    await manager.upsert_document(
        "t1",
        space_key="ops",
        filename="a.md",
        content=content,
        uploader_id="operator-2",
    )

    assert existing.uploader_id == "operator-2"


@pytest.mark.asyncio
async def test_document_list_decorates_updater(monkeypatch):
    document = _FakeDoc(uploader_id="operator-1")
    manager = _patch_common(
        monkeypatch, space=_FakeSpace(), client=_FakeClient()
    )
    monkeypatch.setattr(
        manager.KnowledgeDocument,
        "filter",
        lambda **kwargs: _FakeQuery([document]),
    )

    class _Account:
        id = "operator-1"
        username = "alice"
        display_name = "Alice"

    monkeypatch.setattr(
        manager.Account,
        "filter",
        lambda **kwargs: _FakeQuery([_Account()]),
    )

    result = await manager.list_documents("t1", "ops")

    assert result["items"][0]["updator"] == {
        "username": "alice",
        "display_name": "Alice",
    }


@pytest.mark.asyncio
async def test_search_documents_by_title_returns_space_name(monkeypatch):
    from services.knowledge import manager

    document = _FakeDoc(title="盐田港简介.pptx", space_key="test")
    space = _FakeSpace(space_key="test")
    space.name = "测试空间"

    class Query(_FakeQuery):
        def __init__(self, docs):
            super().__init__(docs)
            self.filters = []

        def filter(self, **kwargs):
            self.filters.append(kwargs)
            return self

    doc_query = Query([document])
    space_query = Query([space])
    initial_filter = {}

    def document_filter(**kwargs):
        initial_filter.update(kwargs)
        return doc_query

    monkeypatch.setattr(manager.KnowledgeDocument, "filter", document_filter)
    monkeypatch.setattr(manager.KnowledgeSpace, "filter", lambda **kwargs: space_query)

    result = await manager.search_documents_by_title("t1", "盐田港")

    assert result["total"] == 1
    assert result["items"][0]["title"] == "盐田港简介.pptx"
    assert result["items"][0]["space_name"] == "测试空间"
    assert initial_filter["title__icontains"] == "盐田港"


@pytest.mark.asyncio
async def test_retry_records_latest_operator(monkeypatch):
    document = _FakeDoc(status="failed", uploader_id="operator-1")
    manager = _patch_common(
        monkeypatch, space=_FakeSpace(), client=_FakeClient(), existing=document
    )

    await manager.retry_document("t1", "doc-1", updater_id="operator-2")

    assert document.uploader_id == "operator-2"


@pytest.mark.asyncio
async def test_delete_document_removes_vector_objects_and_soft_deletes(monkeypatch):
    doc = _FakeDoc()
    client = _FakeClient()
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=client, existing=doc)
    result = await manager.delete_document("t1", "doc-1")
    assert client.deleted[0]["document_id"] == "doc-1"
    assert doc.deleted is True
    assert doc.saved is True
    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_delete_document_succeeds_when_remote_cleanup_fails(monkeypatch):
    doc = _FakeDoc()
    client = _FakeClient()
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=client, existing=doc)

    async def fail_vector(**kwargs):
        raise RuntimeError("qdrant unavailable")

    async def fail_objects(**kwargs):
        raise RuntimeError("object store unavailable")

    client.delete_document = fail_vector
    monkeypatch.setattr(manager.object_store, "delete_objects", fail_objects)

    result = await manager.delete_document("t1", "doc-1")

    assert result == {"deleted": True, "document_id": "doc-1"}
    assert doc.deleted is True
    assert doc.saved is True


@pytest.mark.asyncio
async def test_delete_missing_document_404(monkeypatch):
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=_FakeClient(), existing=None)
    with pytest.raises(CustomError):
        await manager.delete_document("t1", "nope")


def test_upsert_signature_has_no_path_or_url():
    import inspect

    from services.knowledge.manager import upsert_document

    params = inspect.signature(upsert_document).parameters
    for gone in ("rel_path", "url", "instruction"):
        assert gone not in params, f"{gone} 仍在签名里"
    assert "filename" in params


def test_manager_no_longer_imports_status_module():
    import inspect

    from services.knowledge import manager

    source = inspect.getsource(manager)
    assert "status import refresh_pending" not in source
    assert "sync_space" not in source


def test_stale_processing_threshold_defined():
    from services.knowledge.manager import STALE_PROCESSING_MINUTES

    assert STALE_PROCESSING_MINUTES == 30
