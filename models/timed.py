# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Timed(BaseModel):
    """
    运行场景模型
    """

    name = fields.CharField(description="任务名称", max_length=255)
    type = fields.IntField(max_length=1, description="执行类型，0为执行剧本，1为执行动作")
    execute_num = fields.IntField(max_length=10, null=True, description="执行次数")
    success_num = fields.IntField(max_length=10, null=True, description="成功次数")
    playbook = fields.ForeignKeyField("models.Playbook", null=True, related_name="playbook_timed", description="关联剧本")
    playbook_version = fields.CharField(max_length=50, null=True, description="剧本版本")
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
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "type": self.type,
            "playbook_id": self.playbook_id if self.playbook else "",
            "playbook": self.playbook.name if self.playbook else "",
            "playbook_remark": self.playbook.remark if self.playbook else "",
            "playbook_version": self.playbook_version,
            "playbook_input": self.playbook_input,
            "action_id": self.action_id if self.action else "",
            "action": self.action.action["name"] if self.action else "",
            "action_input": self.action_input,
            "status": self.status,
            "tactics": self.tactics,
            "result": self.result,
            "success_rate": self.success_rate,
            "enable": self.enable,
            "end_time": self.end_time,
            "creator": {"username": self.creator.username, "display_name": self.creator.display_name},
            "updator": {"username": self.updator.username, "display_name": self.updator.display_name},
        }

    class Meta:
        table = "timed"
        ordering = ["-ctime"]
