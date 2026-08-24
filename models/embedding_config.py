# coding=utf-8
from tortoise import fields
from models.base import BaseModel


class EmbeddingConfig(BaseModel):
    """OpenViking embedding 配置（全局单例，每个租户一条记录）"""

    class Meta:
        table = "embedding_config"

    # 租户隔离（单租户模式下为默认租户 ID）
    tenant_id = fields.CharField(max_length=36, index=True)

    # 配置内容（TOML 格式）
    content = fields.TextField()
    content_hash = fields.CharField(max_length=64)  # SHA256(content)

    # 维度（从 content 解析，冗余存储便于查询）
    dimension = fields.IntField()

    # 应用状态
    applied_at = fields.DatetimeField(null=True)  # 上次应用到容器的时间
    applied_by = fields.CharField(max_length=36, null=True)  # 操作人 account_id

    # 备注
    remark = fields.TextField(null=True, blank=True)

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "content": self.content,
            "content_hash": self.content_hash,
            "dimension": self.dimension,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "applied_by": self.applied_by,
            "remark": self.remark,
        }
