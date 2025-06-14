# coding=utf-8
from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Approvement(BaseModel):
    """
    人工审核模型
    字段：
    """
    title: str = fields.CharField(max_length=50, index=True, description="审核标题")
    flow = fields.ForeignKeyField("models.PlaybookVersion", description="关联剧本")
    run_id = fields.CharField(max_length=50, description="关联运行ID")
    content = fields.TextField(description="审核描述内容")
    status = fields.IntField(default=0, description="审核状态，0代表待处理，1代表通过，2代表拒绝")
    approve_type = fields.IntField(default=0, description="审核类型，1代表或签，2代表会签")
    expire_time = fields.DatetimeField(description="审核过期时间")
    approvers = fields.ManyToManyField(model_name="models.Account", related_name="approve_accounts")
    audit_node = fields.CharField(max_length=50, description="审核节点ID")
    next_node = fields.CharField(max_length=50, description="下一个要执行的节点ID")
    created_at = fields.DatetimeField(description="审核触发的时间")
    params = fields.JSONField(default=[], description="自定义参数")
    processors = fields.ManyToManyField("models.Account", related_name="approve_processor", null=True, description="实际审核处理人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "title": self.title,
            "content": self.content,
            "status": self.status,
            "expire_time": self.expire_time,
            "approvers": [{"username": i.username, "display_name": i.display_name} for i in self.approvers],
            "params": self.params,
            "processors": [{"username": i.username, "display_name": i.display_name} for i in self.processors] if self.processors else [],
        }

    # 指定name+type作为唯一性约束
    class Meta:
        table = "approvement"
        ordering = ["-utime"]
