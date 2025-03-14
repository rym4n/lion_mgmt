# coding=utf-8

from tortoise import fields, ForeignKeyFieldInstance

from models import Account
from models.base import BaseModel


class Variables(BaseModel):
    """
    全局变量模型，包含以下属性：变量名、变量类型、变量值
    """

    name: str = fields.CharField(max_length=255, description="变量名")
    type: str = fields.CharField(max_length=10, description="变量类型，枚举型： list, set, ipv4, ipv6")
    creator: ForeignKeyFieldInstance["Account"] = fields.ForeignKeyField("models.Account", description="创建人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime.astimezone()),
            "utime": str(self.utime.astimezone()),
            "name": self.name,
            "type": self.type
        }

    # 指定name+type作为唯一性约束
    class Meta:
        ordering = ["utime"]


class Elements(BaseModel):
    """
    全局变量一般是个列表，可以有多个元素
    """
    ele_value: str = fields.CharField(max_length=255, description="元素值")
    updator: ForeignKeyFieldInstance["Account"] = fields.ForeignKeyField("models.Account", description="更新人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime.astimezone()),
            "utime": str(self.utime.astimezone()),
            "value": self.ele_value,
            "updator": self.updator
        }
