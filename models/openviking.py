# coding=utf-8
"""OpenViking 租户凭证管理模型。"""

from models.base import BaseModel, EncryptedCharField
from tortoise import fields


class TenantOpenVikingCredential(BaseModel):
    """租户 OpenViking 知识库凭证。

    每个租户对应一个 OpenViking account，用于隔离知识库数据。
    凭证存储采用加密列，支持轮换（credential_version 递增）。
    """

    tenant_id = fields.CharField(max_length=36, unique=True, description="租户 ID（外键）")
    account_id = fields.CharField(max_length=128, description="OpenViking account ID")
    user_id = fields.CharField(max_length=128, default="default", description="OpenViking user ID")
    api_key = EncryptedCharField(max_length=2048, description="OpenViking API key（加密存储）")
    credential_version = fields.IntField(default=1, description="凭证版本号（轮换时递增）")

    def validate(self) -> None:
        """验证模型字段（预留）。"""
        pass

    @property
    def json(self) -> dict:
        """序列化为 JSON，不包含 api_key。"""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "account_id": self.account_id,
            "user_id": self.user_id,
            "credential_version": self.credential_version,
            "ctime": self.ctime,
            "utime": self.utime,
        }

    class Meta:
        table = "tenant_openviking_credential"
        ordering = ["-utime"]
