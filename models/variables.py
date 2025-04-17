# coding=utf-8

from tortoise import fields

from models.base import BaseModel


class Variables(BaseModel):
    """
    全局变量模型，包含以下属性：变量名、变量类型、变量值(必须是JSON格式）、更新人
    """

    name: str = fields.CharField(max_length=255, unique=True, index=True, description="变量名")
    value: str = fields.JSONField(default=[], description="变量值")
    updator = fields.ForeignKeyField("models.Account", to_field='id', description="更新人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "name": self.name,
            "value": self.value,
            "updator": self.updator.username if self.updator else ""
        }

    # 指定name+type作为唯一性约束
    class Meta:
        table = "variables"
        ordering = ["-utime"]
