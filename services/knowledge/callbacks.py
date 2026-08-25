# coding=utf-8
"""lion_agent 入库完成后的状态回调（SPEC §3.5 D5）。

取代 OpenViking 时代的 task_status 轮询 + Kafka 定时 sweep。
回调丢失由 manager 的 STALE_PROCESSING_MINUTES 惰性兜底。
"""

from datetime import datetime

from sanic.log import logger

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.knowledge import KnowledgeDocument
from settings import TZ

_TERMINAL_STATUS = {"ready", "failed"}
_ERROR_LIMIT = 1000


async def apply_ingest_result(
    tenant_id: str, document_id: str, data: dict
) -> dict:
    """把 lion_agent 的入库结果写回文档记录。"""
    status = str((data or {}).get("status") or "")
    if status not in _TERMINAL_STATUS:
        raise CustomError(ErrorCode.PARAM_ERROR)

    document = await KnowledgeDocument.get_or_none(
        id=document_id, tenant_id=tenant_id, deleted=False
    )
    if not document:
        # 租户不匹配与文档不存在返回同一个错误：document_id 可枚举，
        # 区分开会泄露「该 id 在别的租户下存在」。
        raise CustomError(ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND)

    document.status = status
    if status == "ready":
        document.indexed_at = datetime.now(TZ)
        document.error = None
    else:
        document.error = str(data.get("error") or "")[:_ERROR_LIMIT]
    await document.save()

    logger.info(
        "knowledge_ingest_callback tenant=%s doc=%s status=%s",
        tenant_id, document_id, status,
    )
    return document.json
