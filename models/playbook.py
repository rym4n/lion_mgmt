# coding=utf-8

from tortoise import fields

from models.base import BaseModel


class Playbook(BaseModel):
    """
    剧本模型
    """
    name = fields.CharField(max_length=255, description="剧本名称")
    remark = fields.TextField(description="描述")
    status = fields.IntField(max_length=1, default=0, description="剧本状态,0草稿,1已发布")
    tags = fields.JSONField(default=[], description="标签")
    scenes = fields.ManyToManyField("models.Scene", through="playbook_scene")
    flow_json = fields.JSONField(default={}, description="流程图flow数据，包含edges和nodes")
    version = fields.CharField(max_length=30, default=0, description="版本号，每次publish都会生成新纪录，用于版本回溯")
    creator = fields.ForeignKeyField("models.Account", related_name="created_playbooks", description="创建人")
    updator = fields.ForeignKeyField("models.Account", related_name="updated_playbooks", description="更新人")
    internal = fields.BooleanField(default=False, description="是否内置剧本")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "name": self.name,
            "remark": self.remark,
            "status": self.status,
            "tags": self.tags,
            "scenes": [scene.name for scene in self.scenes] if self.scenes else [],
            "flow_json": self.flow_json,
            "creator": self.updator.username if self.updator else "",
            "updator": self.updator.username if self.updator else "",
            "internal": self.internal
        }

    @property
    def simple_json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "name": self.name,
            "remark": self.remark,
            "status": self.status,
            "tags": self.tags,
            "scenes": [scene.name for scene in self.scenes] if self.scenes else [],
            "creator": self.updator.username if self.updator else "",
            "updator": self.updator.username if self.updator else ""
        }

    class Meta:
        table = "playbook"
        ordering = ["-utime"]

        unique_together = (("name", "version"),)
