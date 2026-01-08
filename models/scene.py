# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Scene(BaseModel):
    """
    运行场景模型
    """

    name = fields.CharField(description="场景名称", unique=True, max_length=255)
    remark = fields.CharField(description="描述", max_length=255)
    status = fields.IntField(max_length=1, description="是否启用，1启用，0禁用")
    creator = fields.ForeignKeyField("models.Account", related_name="created_scenes", description="创建人")
    updator = fields.ForeignKeyField("models.Account", related_name="updated_scenes", description="更新人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "remark": self.remark,
            "status": self.status,
            "creator": {
                "username": self.creator.username,
                "display_name": self.creator.display_name,
            },
            "updator": {
                "username": self.updator.username,
                "display_name": self.updator.display_name,
            },
        }

    # 指定name+type作为唯一性约束
    class Meta:
        table = "scene"
        ordering = ["-ctime"]
