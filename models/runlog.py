# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Runlog(BaseModel):
    """
    所有节点的执行记录
    """
    run_id = fields.CharField(max_length=36, description="每次运行的唯一ID")

    node_id = fields.CharField(max_length=36, description="节点ID")
    node_name = fields.CharField(max_length=255, description="节点名称")
    node_type = fields.CharField(max_length=36, description="节点类型")

    tool_name = fields.CharField(null=True, max_length=255, description="工具标识")
    tool_version = fields.CharField(null=True, max_length=255, description="工具版本")
    tool_display_name = fields.CharField(null=True, max_length=255, description="工具中文名称")

    action_name = fields.CharField(null=True, max_length=255, description="动作标识")
    action_display_name = fields.CharField(null=True, max_length=255, description="动作中文名称")

    status = fields.IntField(default=0, description="执行状态")
    args = fields.JSONField(null=True, description="执行入参")
    result = fields.JSONField(null=True, description="输出结果")

    start_time = fields.DatetimeField(null=True, description="开始时间")
    end_time = fields.DatetimeField(null=True, description="结束时间")

    executor = fields.CharField(null=True, max_length=36, description="执行者ID")

    playbook = fields.ForeignKeyField("models.Playbook", related_name="playbook_runlog", description="关联剧本")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "run_id": self.run_id,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "tool_display_name": self.tool_display_name,
            "action_name": self.action_name,
            "action_display_name": self.action_display_name,
            "status": self.status,
            "args": self.args,
            "result": self.result,
            "start_time": strip_timezone_to_seconds(self.start_time),
            "end_time": strip_timezone_to_seconds(self.end_time),
            "playbook": self.playbook.id if self.playbook else "",
            "creator": self.executor
        }

    class Meta:
        table = "runlog"
        ordering = ["-utime"]

        unique_together = (("run_id", "node_id"),)
