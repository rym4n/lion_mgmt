# coding=utf-8
"""知识库索引状态与重建端点（SPEC §7.1 D9）。

**这里不提供 embedding 配置的写入**——配置在「系统设置 - 模型配置」里
以 purpose='embedding' 维护（llm_model_config 表），知识库直接读那份。
再开一处配置入口等于让用户在两处配同一件事，必然漂移。

本模块只负责：
  1. 报告当前 embedding 配置状态与索引是否需要重建
  2. 换模型后丢弃旧 collection，为重建让路
"""

from datetime import datetime

from sanic import Blueprint, Request

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.tenant_embedding import (
    TenantEmbeddingIndexState,
    compute_fingerprint,
    get_embedding_config,
    get_embedding_payload,
    get_embedding_status,
)
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
    """丢弃当前 collection，为换模型后的重建让路。

    只丢弃，不重灌：重新入库由用户逐篇重试或后续批量任务驱动。
    """
    from services.knowledge.client import get_knowledge_client

    tenant_id = _tenant(request)
    embedding = await get_embedding_payload(tenant_id)
    if embedding is None:
        raise CustomError(ErrorCode.KNOWLEDGE_EMBEDDING_NOT_CONFIGURED)

    result = await get_knowledge_client().rebuild(
        tenant_id=tenant_id, embedding=embedding
    )

    config = await get_embedding_config(tenant_id)
    fingerprint = compute_fingerprint(
        config.base_url, config.model_name, config.dimensions
    )
    state = await TenantEmbeddingIndexState.get_or_none(
        tenant_id=tenant_id, deleted=False
    )
    if state is None:
        state = TenantEmbeddingIndexState(tenant_id=tenant_id)
    # 记账：collection 已按当前指纹重置，不再提示需要重建。
    state.indexed_fingerprint = fingerprint
    state.rebuilt_at = datetime.now(TZ)
    state.rebuilt_by = _operator_id(request)
    await state.save()
    return response_ok(result)
