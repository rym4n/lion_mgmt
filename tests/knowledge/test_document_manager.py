# coding=utf-8
"""文档上传、幂等、删除与配额（SPEC §10.3、§10.5、§10.6）。"""

import hashlib

import pytest

from libs.error_code.exception import CustomError


class _FakeSpace:
    def __init__(self, space_key="ops", tenant_id="t1"):
        self.id = "space-1"
        self.space_key = space_key
        self.tenant_id = tenant_id
        self.name = "运维"
        self.deleted = False

    @property
    def root_uri(self):
        return f"viking://resources/spaces/{self.space_key}"


class _FakeDoc:
    def __init__(self, **kw):
        self.id = kw.get("id", "doc-1")
        self.tenant_id = kw.get("tenant_id", "t1")
        self.space_key = kw.get("space_key", "ops")
        self.rel_path = kw.get("rel_path", "a.md")
        self.uri = kw.get("uri", "viking://resources/spaces/ops/a.md")
        self.title = kw.get("title", "a.md")
        self.content_hash = kw.get("content_hash", "")
        self.size = kw.get("size", 0)
        self.status = kw.get("status", "pending")
        self.task_id = kw.get("task_id")
        self.error = kw.get("error")
        self.source = kw.get("source", "manual")
        self.deleted = False
        self.saved = False

    async def save(self):
        self.saved = True

    @property
    def json(self):
        return {"id": self.id, "status": self.status, "title": self.title}


class _FakeClient:
    def __init__(self):
        self.uploads = []
        self.removed = []

    async def upload_file(self, *, to, filename, content, instruction=None):
        self.uploads.append({"to": to, "filename": filename, "size": len(content)})
        return {"task_id": "task-1"}

    async def add_url(self, *, to, url, instruction=None):
        self.uploads.append({"to": to, "url": url})
        return {"task_id": "task-2"}

    async def rm(self, uri):
        self.removed.append(uri)
        return {}


def _patch_common(monkeypatch, space=None, client=None, existing=None):
    from services.knowledge import manager

    class _FakeQuery:
        async def all(self):
            return []

    async def fake_get_space(tenant_id, space_key):
        if space is None:
            raise CustomError(
                __import__("core.error_code", fromlist=["ErrorCode"]).ErrorCode.KNOWLEDGE_SPACE_NOT_FOUND
            )
        return space

    async def fake_get_client(tenant_id):
        return client

    async def fake_doc_get_or_none(**kwargs):
        return existing

    monkeypatch.setattr(manager, "get_space_or_404", fake_get_space)
    monkeypatch.setattr(manager, "get_knowledge_client", fake_get_client)
    monkeypatch.setattr(
        manager.KnowledgeDocument, "get_or_none", fake_doc_get_or_none
    )
    monkeypatch.setattr(manager.KnowledgeDocument, "filter", lambda **kwargs: _FakeQuery())
    return manager


@pytest.mark.asyncio
async def test_upsert_rejects_disallowed_suffix(monkeypatch):
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=_FakeClient())

    with pytest.raises(CustomError):
        await manager.upsert_document(
            "t1", space_key="ops", rel_path="evil.py", content=b"import os"
        )


@pytest.mark.asyncio
async def test_upsert_rejects_oversized_file(monkeypatch):
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=_FakeClient())
    oversized = b"x" * (manager.MAX_FILE_SIZE + 1)

    with pytest.raises(CustomError):
        await manager.upsert_document(
            "t1", space_key="ops", rel_path="big.md", content=oversized
        )


@pytest.mark.asyncio
async def test_upsert_rejects_path_traversal(monkeypatch):
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=_FakeClient())

    with pytest.raises(CustomError):
        await manager.upsert_document(
            "t1", space_key="ops", rel_path="../../etc/passwd", content=b"x"
        )


@pytest.mark.asyncio
async def test_upsert_same_hash_skips_reupload(monkeypatch):
    content = b"# runbook"
    digest = hashlib.sha256(content).hexdigest()
    existing = _FakeDoc(content_hash=digest, status="ready")
    client = _FakeClient()
    manager = _patch_common(
        monkeypatch, space=_FakeSpace(), client=client, existing=existing
    )

    result = await manager.upsert_document(
        "t1", space_key="ops", rel_path="a.md", content=content
    )

    assert client.uploads == []
    assert result["status"] == "ready"
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_upsert_changed_content_reuploads(monkeypatch):
    existing = _FakeDoc(content_hash="oldhash", status="ready")
    client = _FakeClient()
    manager = _patch_common(
        monkeypatch, space=_FakeSpace(), client=client, existing=existing
    )

    result = await manager.upsert_document(
        "t1", space_key="ops", rel_path="a.md", content=b"# new content"
    )

    assert len(client.uploads) == 1
    assert client.uploads[0]["to"] == "viking://resources/spaces/ops/a.md"
    assert existing.status == "processing"
    assert existing.task_id == "task-1"
    assert result["status"] == "processing"


@pytest.mark.asyncio
async def test_upsert_creates_new_document(monkeypatch):
    created = {}
    client = _FakeClient()
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=client)

    async def fake_create(**kwargs):
        created.update(kwargs)
        return _FakeDoc(**{k: v for k, v in kwargs.items() if k != "space"})

    monkeypatch.setattr(manager.KnowledgeDocument, "create", fake_create)

    await manager.upsert_document(
        "t1", space_key="ops", rel_path="new.md", content=b"# new", source="api"
    )

    assert created["uri"] == "viking://resources/spaces/ops/new.md"
    assert created["source"] == "api"
    assert created["status"] == "processing"
    assert len(client.uploads) == 1


@pytest.mark.asyncio
async def test_upsert_without_client_raises_not_provisioned(monkeypatch):
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=None)

    with pytest.raises(CustomError):
        await manager.upsert_document(
            "t1", space_key="ops", rel_path="a.md", content=b"x"
        )


@pytest.mark.asyncio
async def test_delete_document_removes_from_openviking_and_soft_deletes(monkeypatch):
    doc = _FakeDoc()
    client = _FakeClient()
    manager = _patch_common(monkeypatch, space=_FakeSpace(), client=client, existing=doc)

    result = await manager.delete_document("t1", "doc-1")

    assert client.removed == ["viking://resources/spaces/ops/a.md"]
    assert doc.deleted is True
    assert doc.saved is True
    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_delete_missing_document_404(monkeypatch):
    manager = _patch_common(
        monkeypatch, space=_FakeSpace(), client=_FakeClient(), existing=None
    )

    with pytest.raises(CustomError):
        await manager.delete_document("t1", "nope")
