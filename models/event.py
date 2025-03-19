# coding=utf-8
from typing import List, Dict, Any

from tortoise import fields

from models import BaseModel


class Events(BaseModel):
    """
    事件列表
    """

    event_name: str = fields.CharField(max_length=255, null=True, description="事件名称")
    event_process_status: str = fields.CharField(max_length=50, null=True,
                                                 description="事件处理状态（如 ASSIGNED, RESOLVED）")
    event_source: str = fields.CharField(max_length=255, null=True, description="事件来源")
    event_level: str = fields.CharField(max_length=50, description="事件等级")
    owner: List[Dict[str, Any]] = fields.JSONField(default=list, description="负责人（包括角色和用户）")
    created_id: str = fields.CharField(max_length=36, description="创建人ID，关联用户表")
    event_summary: str = fields.TextField(null=True, description="事件概要")
    log_data: str = fields.TextField(default=dict, description="日志数据")
    event_ingestion_id: str = fields.CharField(max_length=255, description="事件接入id，管理事件接入表")

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
            "event_level_id": self.event_level,
            "owner": self.owner,
            "created_id": self.created_id,
            "event_summary": self.event_summary,
            "log_data": self.log_data,
            "event_ingestion_id": self.event_ingestion_id
        }

    class Meta:
        table = "events"


class EventsIngestion(BaseModel):
    """
    事件接入列表
    """

    event_ingestion_name: str = fields.CharField(max_length=255, null=True, unique=True, description="事件接入名称")
    event_ingestion_describe: str = fields.CharField(max_length=255, null=True, description="事件接入描述")
    event_source_type: str = fields.CharField(max_length=255, null=True, description="事件来源类型")
    transforms_type: str = fields.CharField(max_length=255, null=True, description="转换类型")
    enabled_status: int = fields.IntField(max_length=50, null=True, description="启用状态")
    release_status: int = fields.IntField(max_length=50, null=True, description="发布状态")
    created_id: str = fields.CharField(max_length=36, description="创建人ID，关联用户表")
    file_path: str = fields.CharField(max_length=255, null=True, description="配置文件路径")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "event_ingestion_name": self.event_ingestion_name,
            "event_ingestion_describe": self.event_ingestion_describe,
            "event_source_type": self.event_source_type,
            "transforms_type": self.transforms_type,
            "enabled_status": self.enabled_status,
            "release_status": self.release_status,
            "created_id": self.created_id,
            "file_path": self.file_path
        }

    class Meta:
        table = "events_ingestion"
