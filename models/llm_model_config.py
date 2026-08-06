# coding=utf-8
from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel, EncryptedCharField


def mask_api_key(value: str | None) -> str:
    """脱敏 API Key：保留前 3 后 4，中间固定掩码。明文永不出后端。"""
    if not value:
        return ""
    if len(value) <= 7:
        return "***"
    return f"{value[:3]}***{value[-4:]}"


class LlmModelConfig(BaseModel):
    """LLM 模型配置。一条记录 = 一个端点 + 一个模型名，开箱即用。"""

    name: str = fields.CharField(max_length=128, description="配置名称，如「宏云-aioe」")
    base_url: str = fields.CharField(max_length=512, description="OpenAI 兼容端点")
    api_key: str = EncryptedCharField(max_length=2048, description="API Key，加密存储")
    model_name: str = fields.CharField(max_length=128, description="模型名，如 aioe")
    purpose: str = fields.CharField(max_length=32, null=True, description="用途：chat 主力 | lite 轻量 | null 仅员工指定")
    enabled: bool = fields.BooleanField(default=True, description="是否启用")
    remark: str = fields.CharField(max_length=512, null=True, description="备注")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        """对外序列化，api_key 一律脱敏。"""
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "base_url": self.base_url,
            "api_key": mask_api_key(self.api_key),
            "model_name": self.model_name,
            "purpose": self.purpose,
            "enabled": self.enabled,
            "remark": self.remark,
        }

    class Meta:
        table = "llm_model_config"
        unique_together = (("tenant_id", "name"),)
        ordering = ["-utime"]
