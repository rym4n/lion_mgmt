# coding=utf-8

from tortoise import fields

from models.base import BaseModel


class Playbook(BaseModel):
    """
    剧本模型
    """

    name = fields.CharField(description="剧本名称")
    remark = fields.TextField(description="描述")
    status = fields.IntField(max_length=1, description="剧本状态")
    start_app = fields.CharField(max_length=36, description="开始节点")
    end_app = fields.CharField(max_length=36, description="结束节点")
    if_list = fields.TextField(description="判断节点")
    audit_list = fields.TextField(description="审批节点")
    tags = fields.JSONField(default=[], description="标签")
    scenes: fields.ManyToManyRelation["Scene"] = fields.ManyToManyField("models.Scene", through="playbook_scene")
    flow_json = fields.JSONField(default={}, description="流程图布局信息")
    flow_data = fields.JSONField(default={}, description="各节点详细信息")
    creator = fields.ForeignKeyField("models.Account", description="创建人")
    updator = fields.ForeignKeyField("models.Account", description="更新人")

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
            "creator": self.updator.username if self.updator else "",
            "updator": self.updator.username if self.updator else ""
        }

    # 指定name+type作为唯一性约束
    class Meta:
        table = "playbook"
        ordering = ["-utime"]
