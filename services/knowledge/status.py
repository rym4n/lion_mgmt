# coding=utf-8
"""文档索引状态机（SPEC §10.4）。

状态推进有三条途径，缺一不可：
  1. 提交时同步写 —— manager.upsert_document 拿到 task_id 即置 processing
  2. 列表惰性刷新 —— 用户看列表时顺带查 task 状态（本模块 refresh_pending）
  3. 定时兜底     —— 扫超时未完成的记录（本模块 sweep_stale）

第 3 条是必需的：用户传完就关页面，第 2 条永远不触发，
那条记录会永远卡在 processing。
"""

import asyncio
from datetime import datetime, timedelta

from sanic.log import logger

from models.knowledge import KnowledgeDocument
from services.knowledge.client import get_knowledge_client
from settings import TZ

# 超过这个时长仍在 processing，视为失败
STALE_PROCESSING_MINUTES = 30
# 单次列表请求最多顺带刷新多少条
REFRESH_BATCH_LIMIT = 20
# 刷新并发度
REFRESH_CONCURRENCY = 5

_DONE_STATES = {"completed", "success", "succeeded", "done", "ready"}
_FAILED_STATES = {"failed", "error", "cancelled", "canceled"}


async def refresh_document(client, document) -> bool:
    """查一次 task 状态并更新文档。返回是否发生了状态变化。"""
    if not getattr(document, "task_id", None):
        return False

    try:
        result = await client.task_status(document.task_id)
    except Exception as exc:
        logger.warning(
            "knowledge_task_status_failed doc=%s error_type=%s",
            document.id,
            type(exc).__name__,
        )
        return False

    state = str((result or {}).get("status") or "").lower()
    if state in _DONE_STATES:
        document.status = "ready"
        document.error = None
        document.indexed_at = datetime.now(TZ)
        await document.save()
        return True
    if state in _FAILED_STATES:
        document.status = "failed"
        document.error = str((result or {}).get("error") or "索引失败")[:1000]
        await document.save()
        return True
    return False


async def refresh_pending(tenant_id: str, documents: list) -> int:
    """给列表接口用的惰性刷新：只处理 processing/pending，且限量限并发。"""
    pending = [
        doc
        for doc in documents
        if getattr(doc, "status", None) in ("processing", "pending")
        and getattr(doc, "task_id", None)
    ][:REFRESH_BATCH_LIMIT]
    if not pending:
        return 0

    client = await get_knowledge_client(tenant_id)
    if client is None:
        return 0

    semaphore = asyncio.Semaphore(REFRESH_CONCURRENCY)

    async def _one(doc):
        async with semaphore:
            return await refresh_document(client, doc)

    results = await asyncio.gather(
        *[_one(doc) for doc in pending], return_exceptions=True
    )
    return sum(1 for result in results if result is True)


async def sweep_stale(tenant_id: str) -> dict:
    """定时兜底：把卡死的 processing 记录置为 failed。"""
    documents = await KnowledgeDocument.filter(
        tenant_id=tenant_id,
        status__in=["pending", "processing"],
        deleted=False,
    ).all()
    if not documents:
        return {"checked": 0, "refreshed": 0, "failed": 0}

    client = await get_knowledge_client(tenant_id)
    threshold = datetime.now(TZ) - timedelta(minutes=STALE_PROCESSING_MINUTES)

    refreshed = failed = 0
    for document in documents:
        utime = getattr(document, "utime", None)
        is_stale = bool(utime and utime < threshold)

        if client is not None and await refresh_document(client, document):
            refreshed += 1
            continue

        if is_stale and document.status in ("pending", "processing"):
            document.status = "failed"
            document.error = "索引超时，请重试或重新上传"
            await document.save()
            failed += 1

    logger.info(
        "knowledge_status_sweep tenant=%s checked=%d refreshed=%d failed=%d",
        tenant_id,
        len(documents),
        refreshed,
        failed,
    )
    return {"checked": len(documents), "refreshed": refreshed, "failed": failed}
