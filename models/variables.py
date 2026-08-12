# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Variables(BaseModel):
    """
    全局变量模型，包含以下属性：变量名、变量类型、变量值(必须是JSON格式）、更新人
    """

    name: str = fields.CharField(max_length=255, index=True, description="变量名")
    value: str = fields.JSONField(default=[], description="变量值")
    type: str = fields.CharField(max_length=255, null=True, description="变量类型")
    updator = fields.ForeignKeyField("models.Account", to_field="id", description="更新人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "value": self.value,
            "updator": {
                "username": self.updator.username if self.updator.type == "user" else "API账户",
                "display_name": self.updator.display_name if self.updator.type == "user" else self.updator.username,
            },
        }

    class Meta:
        table = "variables"
        ordering = ["-utime"]
