# coding=utf-8
from tortoise import fields

from models.base import BaseModel


class License(BaseModel):
    """
    产品许可表
    """

    data = fields.TextField(description="许可数据")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "data": self.data,
        }

    class Meta:
        table = "license"
