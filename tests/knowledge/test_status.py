# coding=utf-8
"""文档索引状态机（SPEC §10.4）。

「上传后要等一会儿才能搜到」是正常的，产品上要做的是把状态说准。
"""

from datetime import datetime, timedelta

import pytest


class _Doc:
    def __init__(self, status="processing", task_id="task-1", minutes_ago=0):
        self.id = "doc-1"
        self.status = status
        self.task_id = task_id
        self.error = None
        self.indexed_at = None
        self.saved = False
        from settings import TZ

        self.utime = datetime.now(TZ) - timedelta(minutes=minutes_ago)

    async def save(self):
        self.saved = True


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def task_status(self, task_id):
        self.calls.append(task_id)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.mark.asyncio
async def test_refresh_marks_ready_on_success():
    from services.knowledge.status import refresh_document

    doc = _Doc()
    client = _Client({"status": "completed"})

    changed = await refresh_document(client, doc)

    assert changed is True
    assert doc.status == "ready"
    assert doc.indexed_at is not None
    assert doc.saved is True


@pytest.mark.asyncio
async def test_refresh_marks_failed_with_reason():
    from services.knowledge.status import refresh_document

    doc = _Doc()
    client = _Client({"status": "failed", "error": "unsupported encoding"})

    changed = await refresh_document(client, doc)

    assert changed is True
    assert doc.status == "failed"
    assert "unsupported encoding" in doc.error


@pytest.mark.asyncio
async def test_refresh_keeps_processing_when_running():
    from services.knowledge.status import refresh_document

    doc = _Doc()
    client = _Client({"status": "running"})

    changed = await refresh_document(client, doc)

    assert changed is False
    assert doc.status == "processing"


@pytest.mark.asyncio
async def test_refresh_skips_document_without_task_id():
    """没有 task_id 的记录查不了状态，不该白白发一次请求。"""
    from services.knowledge.status import refresh_document

    doc = _Doc(task_id=None)
    client = _Client({"status": "completed"})

    changed = await refresh_document(client, doc)

    assert changed is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_refresh_batch_respects_limit(monkeypatch):
    """列表接口顺带刷新时要限量，否则一页 100 条就是 100 次外部调用，
    列表接口会被拖垮。"""
    from services.knowledge import status

    docs = [_Doc() for _ in range(50)]
    client = _Client({"status": "running"})

    async def fake_get_client(tenant_id):
        return client

    monkeypatch.setattr(status, "get_knowledge_client", fake_get_client)

    await status.refresh_pending("t1", docs)

    assert len(client.calls) <= status.REFRESH_BATCH_LIMIT


@pytest.mark.asyncio
async def test_sweep_marks_stale_processing_as_failed(monkeypatch):
    """用户传完就关页面时惰性刷新永不触发，
    没有这个兜底，那条记录会永远卡在 processing。"""
    from services.knowledge import status

    stale = _Doc(minutes_ago=status.STALE_PROCESSING_MINUTES + 5)
    fresh = _Doc(minutes_ago=1)

    class _QS:
        async def all(self):
            return [stale, fresh]

        def filter(self, **kwargs):
            return self

    monkeypatch.setattr(
        status.KnowledgeDocument, "filter", lambda **kw: _QS()
    )

    client = _Client(Exception("task not found"))

    async def fake_get_client(tenant_id):
        return client

    monkeypatch.setattr(status, "get_knowledge_client", fake_get_client)

    result = await status.sweep_stale("t1")

    assert stale.status == "failed"
    assert "超时" in stale.error
    assert fresh.status == "processing"
    assert result["failed"] == 1
