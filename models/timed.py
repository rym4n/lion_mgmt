# coding=utf-8

from tortoise import fields

from models.base import BaseModel


class Timed(BaseModel):
    """
    运行场景模型
    """

    name = fields.CharField(description="任务名称", max_length=255)
    type = fields.IntField(max_length=1, description="执行类型，0为执行剧本，1为执行动作")
    playbook = fields.ForeignKeyField("models.Playbook", null=True, related_name="playbook_timed", description="关联剧本")
    playbook_input = fields.JSONField(null=True, description="剧本输入参数")
    action = fields.ForeignKeyField("models.ToolAction", null=True, related_name="action_timed", description="关联工具动作")
    action_input = fields.JSONField(null=True, description="工具动作输入参数")
    status = fields.IntField(max_length=1, description="执行状态，0为单次执行，1为周期执行")
    tactics = fields.CharField(max_length=50, description="执行策略")
    result = fields.CharField(max_length=50, null=True, description="执行结果")
    success_rate = fields.CharField(max_length=10, null=True, description="成功率")
    enable = fields.IntField(max_length=1, default=1, description="是否启用, 0为禁用，1为启用")
    end_time = fields.CharField(max_length=50, description="结束时间")
    creator = fields.ForeignKeyField("models.Account", related_name="created_timed", description="创建人")
    updator = fields.ForeignKeyField("models.Account", related_name="updated_timed", description="更新人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "name": self.name,
            "type": self.type,
            "playbook": self.playbook.name if self.playbook else "",
            "playbook_input": self.playbook_input,
            "action": self.action.action["description"] if self.action else "",
            "action_input": self.action_input,
            "status": self.status,
            "tactics": self.tactics,
            "result": self.result,
            "success_rate": self.success_rate,
            "enable": self.enable,
            "end_time": self.end_time,
            "creator": self.creator.username if self.creator else "",
            "updator": self.updator.username if self.updator else "",
        }

    class Meta:
        table = "timed"
        ordering = ["-utime"]
