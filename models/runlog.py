# coding=utf-8

from tortoise import fields

from models.base import BaseModel


class Runlog(BaseModel):
    """
    所有节点的执行记录
    """
    run_id = fields.CharField(max_length=36, description="每次运行的唯一ID")

    node_id = fields.CharField(max_length=36, description="节点ID")
    node_name = fields.CharField(max_length=255, description="节点名称")
    node_type = fields.CharField(max_length=36, description="节点类型")

    tool_id = fields.CharField(max_length=36, description="工具ID")
    tool_name = fields.CharField(max_length=255, description="工具标识")
    tool_display_name = fields.CharField(max_length=255, description="工具中文名称")

    action_id = fields.CharField(max_length=36, description="动作ID")
    action_name = fields.CharField(max_length=255, description="动作标识")
    action_display_name = fields.CharField(max_length=255, description="动作中文名称")

    status = fields.IntField(default=0, description="执行状态")
    args = fields.JSONField(default=[], description="执行入参")
    result = fields.JSONField(default=[], description="输出结果")

    start_time = fields.DatetimeField(null=False, auto_now_add=True, description="开始时间")
    end_time = fields.DatetimeField(null=False, auto_now_add=True, description="结束时间")

    playbook = fields.ForeignKeyField("models.Playbook", related_name="playbook_runlog", description="关联剧本")
    creator = fields.ForeignKeyField("models.Account", related_name="account_run", description="执行人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "run_id": self.run_id,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "tool_display_name": self.tool_display_name,
            "action_id": self.action_id,
            "action_name": self.action_name,
            "action_display_name": self.action_display_name,
            "status": self.status,
            "args": self.args,
            "result": self.result,
            "start_time": str(self.start_time),
            "end_time": str(self.end_time),
            "playbook": self.playbook.simple_json(),
            "creator": self.creator.simple_json()
        }

    class Meta:
        table = "runlog"
        ordering = ["-utime"]
