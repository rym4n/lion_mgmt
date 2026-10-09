# coding=utf-8
"""知识库索引状态与重建端点（SPEC §7.1 D9）。

**这里不提供 embedding 配置的写入**——配置在「系统设置 - 模型配置」里
以 purpose='embedding' 维护（llm_model_config 表），知识库直接读那份。
再开一处配置入口等于让用户在两处配同一件事，必然漂移。

本模块只负责：
  1. 报告当前 embedding 配置状态与索引是否需要重建
  2. 在不改变向量空间的前提下重建单个空间
"""

import asyncio
from datetime import datetime

from sanic import Blueprint, Request
from sanic.log import logger

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.knowledge import KnowledgeDocument
from models.tenant_embedding import (
    TenantEmbeddingIndexState,
    compute_fingerprint,
    get_embedding_payload,
    get_embedding_status,
)
from services.knowledge.client import get_knowledge_client
from services.knowledge import manager
from settings import TZ

knowledge_embedding_bp = Blueprint(
    "knowledge_embedding", url_prefix="/knowledge/embedding-config"
)


def _tenant(request: Request) -> str:
    tenant_id = request.ctx.session.get("tenant_id")
    if not tenant_id:
        raise CustomError(ErrorCode.FORBIDDEN)
    return str(tenant_id)


def _operator_id(request: Request) -> str | None:
    account = getattr(request.ctx, "session", {}).get("account_info")
    if isinstance(account, dict):
        return account.get("id")
    return getattr(account, "id", None)


@knowledge_embedding_bp.get("")
@requires_permission("knowledge:r")
async def get_config(request: Request):
    """当前 embedding 配置状态。未配置时 configured=False。"""
    return response_ok(await get_embedding_status(_tenant(request)))


@knowledge_embedding_bp.post("/rebuild")
@requires_permission("knowledge:u")
async def rebuild_index(request: Request):
    """Clear and resubmit only the selected space's documents."""
    tenant_id = _tenant(request)
    space_key = str((request.json or {}).get("space_key") or "")
    if not space_key:
        raise CustomError(ErrorCode.PARAM_ERROR)
    await manager.get_space_or_404(tenant_id, space_key)

    embedding = await get_embedding_payload(tenant_id)
    if embedding is None:
        raise CustomError(ErrorCode.KNOWLEDGE_EMBEDDING_NOT_CONFIGURED)

    fingerprint = compute_fingerprint(
        embedding["base_url"], embedding["model"], embedding["dimensions"]
    )

    state = await TenantEmbeddingIndexState.get_or_none(
        tenant_id=tenant_id, deleted=False
    )
    if state and state.indexed_fingerprint and state.indexed_fingerprint != fingerprint:
        raise CustomError(ErrorCode.KNOWLEDGE_SPACE_REBUILD_REQUIRES_TENANT_MIGRATION)

    documents = await KnowledgeDocument.filter(
        tenant_id=tenant_id, space_key=space_key, deleted=False
    ).all()
    client = get_knowledge_client()
    result = await client.rebuild_space(
        tenant_id=tenant_id,
        space_key=space_key,
        document_ids=[str(document.id) for document in documents],
        embedding=embedding,
    )

    # The collection is shared, but only this space's document IDs are cleared.
    await KnowledgeDocument.filter(
        tenant_id=tenant_id,
        space_key=space_key,
        deleted=False,
    ).update(
        status="failed",
        error="索引已重建，正在重新提交",
        indexed_at=None,
        ingest_attempt_id=None,
    )

    if state is None:
        state = TenantEmbeddingIndexState(tenant_id=tenant_id)
    # The selected space uses the current model. Other spaces remain untouched.
    state.indexed_fingerprint = fingerprint
    state.rebuilt_at = datetime.now(TZ)
    state.rebuilt_by = _operator_id(request)
    await state.save()

    semaphore = asyncio.Semaphore(8)

    async def submit(document: KnowledgeDocument) -> bool:
        async with semaphore:
            try:
                await manager.submit_document_ingest(
                    tenant_id, document, embedding, client=client
                )
                return True
            except Exception as exc:
                logger.warning(
                    "knowledge_rebuild_submit_failed tenant=%s doc=%s error_type=%s",
                    tenant_id, document.id, type(exc).__name__,
                )
                return False

    submitted = await asyncio.gather(*(submit(doc) for doc in documents))
    return response_ok({
        **result,
        "space_key": space_key,
        "document_count": len(documents),
        "submitted_count": sum(submitted),
        "failed_count": len(documents) - sum(submitted),
    })
