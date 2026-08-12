# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel, EncryptedCharField, parse_encrypted_json


class Config(BaseModel):
    """
    通用配置表
    """

    key: str = fields.CharField(max_length=255, unique=True, description="配置项")
    value = EncryptedCharField(max_length=4000, null=False, description="配置值")
    type: str = fields.CharField(max_length=255, null=True, description="配置状态")
    module: str = fields.CharField(max_length=255, description="所属模块")
    shared: bool = fields.BooleanField(default=False, description="共享标识")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json_value(self):
        return parse_encrypted_json(self.value)

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
            "shared": self.shared,
        }

    class Meta:
        table = "config"
        ordering = ["-utime"]
