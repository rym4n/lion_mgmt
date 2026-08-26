# coding=utf-8
"""知识库 embedding 配置的读取与向量空间指纹（SPEC §7.1 D9）。

**配置来源是既有的 `llm_model_config` 表**（系统设置 - 模型配置里
purpose='embedding' 的那条），不是另起一张表。该表本来就有 purpose /
dimensions / 加密 api_key 三个字段，正是知识库需要的全部信息；
再建一份等于让用户在两处配同一件事，且必然漂移。

本模块只额外持有「指纹记账」——记录当前 Qdrant collection 实际是用
哪个向量空间建的，用于判断换模型后是否需要重建。
"""

import hashlib

from models.base import BaseModel
from models.llm_model_config import LlmModelConfig
from tortoise import fields

EMBEDDING_PURPOSE = "embedding"


def compute_fingerprint(base_url: str, model: str, dimensions: int) -> str:
    """向量空间指纹。

    必须与 lion_agent 的 knowledge.embedding.embedding_fingerprint 算法
    逐字一致（同样的拼接顺序、同样的 sha256 前 16 位），否则两侧对不上账，
    换模型后无法判断是否需要重建 collection。
    刻意不含 api_key：轮换密钥不改变向量空间。
    """
    raw = "\0".join(
        ["OpenAIEmbeddingModel", base_url.rstrip("/"), model, str(dimensions)]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class TenantEmbeddingIndexState(BaseModel):
    """租户知识库索引的向量空间记账。

    只记「当前 collection 是用哪个指纹建的」。embedding 配置本身在
    llm_model_config，不在这里 —— 这张表不是配置的第二份副本。
    """

    tenant_id = fields.CharField(max_length=36, unique=True, index=True)
    indexed_fingerprint = fields.CharField(
        max_length=32,
        default="",
        description="当前 Qdrant collection 实际使用的指纹；与当前配置指纹不等即待重建",
    )
    rebuilt_at = fields.DatetimeField(null=True)
    rebuilt_by = fields.CharField(max_length=36, null=True)

    class Meta:
        table = "tenant_embedding_index_state"
        ordering = ["-utime"]


async def get_embedding_config(tenant_id: str) -> LlmModelConfig | None:
    """取租户启用中的 embedding 模型配置。

    维度缺失一律视为未配置：collection 的向量维度由它决定，猜错会把整个
    collection 建错，且要到检索时才暴露。
    """
    return await LlmModelConfig.get_or_none(
        tenant_id=tenant_id,
        purpose=EMBEDDING_PURPOSE,
        enabled=True,
        deleted=False,
        dimensions__not_isnull=True,
    )


async def get_embedding_payload(tenant_id: str) -> dict | None:
    """组装成下发给 lion_agent 的形状。

    返回 None 表示该租户未配置可用的 embedding 模型——调用方必须据此拒绝
    入库与检索，而不是用某个默认值糊过去。
    """
    config = await get_embedding_config(tenant_id)
    if not config or not config.dimensions:
        return None
    return {
        "base_url": config.base_url,
        "model": config.model_name,
        "dimensions": config.dimensions,
        "api_key": config.api_key,
    }


async def get_embedding_status(tenant_id: str) -> dict:
    """给管理面用的状态视图：当前配置 + 是否需要重建索引。"""
    config = await get_embedding_config(tenant_id)
    state = await TenantEmbeddingIndexState.get_or_none(
        tenant_id=tenant_id, deleted=False
    )
    indexed = state.indexed_fingerprint if state else ""

    if not config or not config.dimensions:
        return {
            "configured": False,
            "indexed_fingerprint": indexed,
            "rebuild_required": False,
        }

    fingerprint = compute_fingerprint(
        config.base_url, config.model_name, config.dimensions
    )
    return {
        "configured": True,
        "name": config.name,
        "model": config.model_name,
        "base_url": config.base_url,
        "dimensions": config.dimensions,
        "fingerprint": fingerprint,
        "indexed_fingerprint": indexed,
        # 从未建过索引(indexed 为空)不算「待重建」——那是首次使用，
        # 只有换过模型/维度才需要提示用户重建。
        "rebuild_required": bool(indexed) and indexed != fingerprint,
    }
