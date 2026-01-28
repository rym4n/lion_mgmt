# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel, EncryptedCharField


class Config(BaseModel):
    """
    通用配置表
    """

    key: str = fields.CharField(max_length=255, unique=True, description="配置项")
    value = EncryptedCharField(max_length=4000, null=False, description="配置值")
    type: str = fields.CharField(max_length=255, null=True, description="配置状态")
    module: str = fields.CharField(max_length=255, description="所属模块")
    default: bool = fields.BooleanField(default=True, description="默认标识")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "key": self.key,
            "value": self.value,
            "type": self.type,
            "module": self.module,
            "default": self.default,
        }

    class Meta:
        table = "config"
        ordering = ["-utime"]
