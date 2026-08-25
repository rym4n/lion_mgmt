# coding=utf-8
"""租户级知识库 embedding 配置端点（SPEC §7.1 D9）。

取代 services/digital_employee/views_embedding.py：那套是「JSON 配置落盘 +
重启 OpenViking 容器」，耗时 30-120 秒。新链路只写一行数据库记录，
配置随请求下发给 lion_agent，秒返回。
"""

from datetime import datetime

from sanic import Blueprint, Request

from core.error_code import ErrorCode
from core.response import response_ok
from libs.error_code.exception import CustomError
from libs.security.authorization import requires_permission
from models.tenant_embedding import TenantEmbeddingConfig, compute_fingerprint
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
    config = await TenantEmbeddingConfig.get_or_none(
        tenant_id=_tenant(request), deleted=False
    )
    return response_ok(config.json if config else None)


@knowledge_embedding_bp.post("")
@requires_permission("knowledge:u")
async def set_config(request: Request):
    """写入配置。api_key 留空表示沿用已存的密钥。"""
    params = request.json or {}
    base_url = str(params.get("base_url") or "").strip()
    model = str(params.get("model") or "").strip()
    dimensions = params.get("dimensions")
    if not base_url or not model or dimensions is None:
        raise CustomError(ErrorCode.PARAM_ERROR)
    try:
        dims = int(dimensions)
    except (TypeError, ValueError):
        raise CustomError(ErrorCode.PARAM_ERROR) from None
    if dims <= 0:
        raise CustomError(ErrorCode.PARAM_ERROR)

    tenant_id = _tenant(request)
    config = await TenantEmbeddingConfig.get_or_none(
        tenant_id=tenant_id, deleted=False
    )
    api_key = str(params.get("api_key") or "")

    if config is None:
        config = TenantEmbeddingConfig(tenant_id=tenant_id)
    config.base_url = base_url
    config.model = model
    config.dimensions = dims
    if api_key:
        # 留空 = 不修改。避免前端不回显密钥时把已存的值清掉。
        config.api_key = api_key
    config.fingerprint = compute_fingerprint(base_url, model, dims)
    config.applied_at = datetime.now(TZ)
    config.applied_by = _operator_id(request)
    config.remark = params.get("remark")
    await config.save()
    return response_ok(config.json)


@knowledge_embedding_bp.post("/rebuild")
@requires_permission("knowledge:u")
async def rebuild_index(request: Request):
    """丢弃当前 collection，为换模型后的重建让路。

    只丢弃，不重灌：重新入库由用户逐篇重试或后续批量任务驱动。
    """
    from models.tenant_embedding import get_embedding_payload
    from services.knowledge.client import get_knowledge_client

    tenant_id = _tenant(request)
    embedding = await get_embedding_payload(tenant_id)
    if embedding is None:
        raise CustomError(ErrorCode.KNOWLEDGE_EMBEDDING_NOT_CONFIGURED)

    result = await get_knowledge_client().rebuild(
        tenant_id=tenant_id, embedding=embedding
    )
    config = await TenantEmbeddingConfig.get_or_none(
        tenant_id=tenant_id, deleted=False
    )
    if config:
        # 记账：collection 已按当前指纹重置，不再提示需要重建。
        config.indexed_fingerprint = config.fingerprint
        await config.save()
    return response_ok(result)
