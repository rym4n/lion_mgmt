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
    approvers = fields.ManyToManyField(model_name="models.Account", related_name="approvers", through="approve_accounts", description="审核人员")
    audit_node = fields.CharField(max_length=50, description="审核节点ID")
    next_node = fields.CharField(max_length=50, description="下一个要执行的节点ID")
    created_at = fields.DatetimeField(description="审核触发的时间")
    params = fields.JSONField(default=[], description="自定义参数")
    agree_count = fields.IntField(default=0, description="审核通过数量")

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
            "expire_time": strip_timezone_to_seconds(self.expire_time),
            "approvers": [{"username": i.username, "display_name": i.display_name} for i in self.approvers],
            "params": self.params,
        }

    # 指定name+type作为唯一性约束
    class Meta:
        table = "approvement"
        ordering = ["-utime"]


class ApprovementProcessor(BaseModel):
    """
    人工审核处理人模型
    字段：
    """
    approvement = fields.ForeignKeyField("models.Approvement")
    processor = fields.ForeignKeyField("models.Account")
    agree = fields.BooleanField(null=False, description="审核意见，True代表通过，False代表拒绝")
    remark = fields.TextField(null=False, description="审核结果备注")

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "agree": self.agree,
            "remark": self.remark,
            "processor": {"username": self.processor.username, "display_name": self.processor.display_name}
        }

    class Meta:
        table = "approve_processor"
