# coding=utf-8

from tortoise import fields

from models.base import BaseModel


class Config(BaseModel):
    """
    通用配置表
    """

    key: str = fields.CharField(max_length=255, unique=True, description="配置项")
    value: str = fields.TextField(description="配置值")
    config_type: str = fields.CharField(max_length=255, null=True,description="配置状态")
    module: str = fields.CharField(max_length=255, description="所属模块")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "key": self.key,
            "value": self.value,
            "config_type": self.config_type,
            "module": self.module
        }

    class Meta:
        table = "config"
        ordering = ["-utime"]
