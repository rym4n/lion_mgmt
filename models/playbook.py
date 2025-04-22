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
    start_app = fields.CharField(max_length=36, description="开始节点")
    end_app = fields.CharField(max_length=36, description="结束节点")
    if_list = fields.TextField(description="判断节点")
    audit_list = fields.TextField(description="审批节点")
    tags = fields.JSONField(default=[], description="标签")
    scenes = fields.ManyToManyField("models.Scene", through="playbook_scene")
    flow_json = fields.JSONField(default={}, description="流程图布局信息")
    flow_data = fields.JSONField(default={}, description="各节点详细信息")
    version = fields.IntField(default=0, description="版本号，每次publish都会生成新纪录，用于版本回溯")
    creator = fields.ForeignKeyField("models.Account", related_name="created_playbooks", description="创建人")
    updator = fields.ForeignKeyField("models.Account", related_name="updated_playbooks", description="更新人")

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
            "start_app": self.start_app,
            "end_app": self.end_app,
            "if_list": self.if_list,
            "audit_list": self.audit_list,
            "tags": self.tags,
            "scenes": [scene.name for scene in self.scenes] if self.scenes else [],
            "flow_json": self.flow_json,
            "flow_data": self.flow_data,
            "creator": self.updator.username if self.updator else "",
            "updator": self.updator.username if self.updator else ""
        }

    class Meta:
        table = "playbook"
        ordering = ["-utime"]

        unique_together = [("name", "version")]
