# coding=utf-8
from typing import List, Dict, Any

from tortoise import fields

from models import BaseModel


class Events(BaseModel):
    """
    事件列表
    """

    event_name: str = fields.CharField(max_length=255, null=True, description="事件名称")
    event_process_status: str = fields.CharField(max_length=50, null=True,description="事件处理状态（如 ASSIGNED, RESOLVED）")
    event_source: str = fields.CharField(max_length=255, null=True, description="事件来源")
    event_level_id: int = fields.IntField(description="事件等级")
    owner: List[Dict[str, Any]] = fields.JSONField(default=list, description="负责人（包括角色和用户）")
    created_id: int = fields.IntField(description="创建人ID，关联用户表")
    event_summary: str = fields.TextField(null=True, description="事件概要")
    log_data: str = fields.TextField(default=dict, description="日志数据")


    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "event_name": self.event_name,
            "event_process_status": self.event_process_status,
            "event_source": self.event_source,
            "event_level_id": self.event_level_id,
            "owner": self.owner,
            "created_id": self.created_id,
            "event_summary": self.event_summary,
            "log_data": self.log_data
        }

    class Meta:
        table = "events"
