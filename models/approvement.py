# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Approvement(BaseModel):
    """Legacy playbook approval record kept for stored ``approval-node`` flows."""

    title: str = fields.CharField(max_length=50, index=True, description="审核标题")
    flow = fields.ForeignKeyField(
        "models.PlaybookVersion",
        null=True,
        description="关联剧本",
    )
    run_id = fields.CharField(max_length=64, description="关联运行ID")
    content = fields.TextField(description="审核描述内容")
    status = fields.IntField(
        default=0,
        description="审核状态，0待处理，1通过，2拒绝，3过期",
    )
    approve_type = fields.IntField(
        default=1,
        description="审核类型，1代表或签，2代表会签",
    )
    expire_time = fields.DatetimeField(description="审核过期时间")
    approvers = fields.ManyToManyField(
        model_name="models.Account",
        related_name="approvers",
        through="approve_accounts",
        description="审核人员",
    )
    audit_node = fields.CharField(max_length=36, description="审核节点ID")
    created_at = fields.DatetimeField(description="审核触发的时间")
    params = fields.JSONField(null=True, description="自定义参数")
    results = fields.JSONField(default=dict, description="处理记录")
    callback_token = fields.CharField(max_length=64, description="外部回调token")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "title": self.title,
            "flow_id": self.flow_id,
            "run_id": self.run_id,
            "content": self.content,
            "status": self.status,
            "expire_time": strip_timezone_to_seconds(self.expire_time),
            "created_at": strip_timezone_to_seconds(self.created_at),
            "approvers": [
                {
                    "id": approver.id,
                    "username": approver.username,
                    "display_name": approver.display_name,
                }
                for approver in self.approvers
            ],
            "approve_type": self.approve_type,
            "results": self.results or {},
            "params": self.params,
            "audit_node": self.audit_node,
            "approval_kind": "playbook",
        }

    class Meta:
        table = "approvement"
        ordering = ["-utime"]
