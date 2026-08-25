# coding=utf-8
"""知识库 embedding 配置，租户级（SPEC §7.1 D9）。

取代旧的 EmbeddingConfig（全局单例 + 维度硬编码 2048）与
旧的外部存储配置模块（写配置文件 + 重启容器）。
新架构下配置只是一份数据，随每次请求下发给 lion_agent，
不落盘、不重启任何容器。
"""

import hashlib

from models.base import BaseModel, EncryptedCharField
from tortoise import fields


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


class TenantEmbeddingConfig(BaseModel):
    """一个租户一份知识库 embedding 配置。"""

    tenant_id = fields.CharField(max_length=36, unique=True, index=True)
    base_url = fields.CharField(max_length=512, description="OpenAI 兼容端点")
    model = fields.CharField(max_length=128, description="模型名")
    dimensions = fields.IntField(description="向量维度，由模型决定，不能猜")
    api_key = EncryptedCharField(max_length=2048, description="加密存储")

    fingerprint = fields.CharField(
        max_length=32, default="", description="向量空间指纹，变化即需重建 collection"
    )
    indexed_fingerprint = fields.CharField(
        max_length=32,
        default="",
        description="当前 Qdrant collection 实际使用的指纹；与 fingerprint 不等即待重建",
    )
    applied_at = fields.DatetimeField(null=True)
    applied_by = fields.CharField(max_length=36, null=True)
    remark = fields.TextField(null=True)

    @property
    def json(self) -> dict:
        """对外序列化，绝不含 api_key。"""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "base_url": self.base_url,
            "model": self.model,
            "dimensions": self.dimensions,
            "fingerprint": self.fingerprint,
            "indexed_fingerprint": self.indexed_fingerprint,
            "rebuild_required": self.fingerprint != self.indexed_fingerprint,
            "applied_at": self.applied_at,
            "ctime": self.ctime,
            "utime": self.utime,
        }

    class Meta:
        table = "tenant_embedding_config"
        ordering = ["-utime"]


async def get_embedding_payload(tenant_id: str) -> dict | None:
    """取租户 embedding 配置，组装成下发给 lion_agent 的形状。

    返回 None 表示该租户未配置——调用方必须据此拒绝入库与检索，
    而不是用某个默认值糊过去：维度猜错会把整个 collection 建错，
    且要到检索时才暴露。
    """
    config = await TenantEmbeddingConfig.get_or_none(
        tenant_id=tenant_id, deleted=False
    )
    if not config:
        return None
    return {
        "base_url": config.base_url,
        "model": config.model,
        "dimensions": config.dimensions,
        "api_key": config.api_key,
    }
