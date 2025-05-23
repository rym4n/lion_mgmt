# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Tag(BaseModel):
    """
    运行场景模型
    """

    name = fields.CharField(description="标签名称", max_length=255)
    type = fields.IntField(max_length=1, description="0为剧本，1为工具")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "type": self.type
        }

    class Meta:
        table = "tag"
        ordering = ["-utime"]
