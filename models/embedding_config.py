# coding=utf-8
from tortoise import fields
from models.base import BaseModel


class EmbeddingConfig(BaseModel):
    """OpenViking embedding 配置（全局单例，维度固定 2048）"""

    class Meta:
        table = "embedding_config"

    # 租户隔离（单租户模式下为默认租户 ID）
    tenant_id = fields.CharField(max_length=36, index=True, unique=True)

    # 配置内容（JSON 格式）
    content = fields.TextField()
    content_hash = fields.CharField(max_length=64)  # SHA256(content)

    # 维度（固定 2048，冗余存储用于审计）
    dimension = fields.IntField(default=2048)

    # 应用状态
    applied_at = fields.DatetimeField(null=True)  # 上次应用到容器的时间
    applied_by = fields.CharField(max_length=36, null=True)  # 操作人 account_id

    # 备注
    remark = fields.TextField(null=True, blank=True)
