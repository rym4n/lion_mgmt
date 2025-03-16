# coding=utf-8
from tortoise import fields

from models.base import BaseModel


class Approvement(BaseModel):
    """
    人工审核模型
    字段：
    """
    title: str = fields.CharField(max_length=50, index=True, description="审核标题")
    content = fields.TextField(description="审核描述内容")
    status = fields.IntField(default=0, description="审核状态，0代表待审核，1代表审核通过，2代表审核拒绝，3代表审核过期")
    expire_time = fields.DatetimeField(null=True, description="审核过期时间")
    approvers = fields.CharField(max_length=255, null=True, description="审核人列表，多个用逗号分隔")
    processor = fields.ForeignKeyField("models.Account", null=True, description="实际审核处理人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "title": self.title,
            "content": self.content,
            "status": self.status,
            "expire_time": self.expire_time,
            "approvers": self.approvers,
            "processor": self.processor.username if self.processor else None
        }

    # 指定name+type作为唯一性约束
    class Meta:
        table = "approvement"
        ordering = ["-utime"]
