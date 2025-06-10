# coding=utf-8
from typing import List, Dict, Any

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Events(BaseModel):
    """
    事件列表
    """

    event_name: str = fields.CharField(max_length=255, unique=True, description="事件名称")
    event_process_status: str = fields.CharField(max_length=50, description="事件处理状态")
    event_source: str = fields.CharField(max_length=255, null=True, description="事件来源")
    owner: Dict[str, Any] = fields.JSONField(default=list, null=True, description="负责人（包括角色和用户）")
    event_summary: str = fields.TextField(null=True, description="事件概要")
    log_data: str = fields.TextField(default=dict, description="日志数据")
    event_ingestion_id: str = fields.CharField(max_length=255, null=True, description="事件接入id，管理事件接入表")
    room = fields.ForeignKeyField("models.Room", null=True, related_name="room_events", description="关联房间")
    updator = fields.ForeignKeyField("models.Account", related_name="updated_events", description="更新人")
    creator = fields.ForeignKeyField("models.Account", related_name="creator_events", description="创建人")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "event_name": self.event_name,
            "event_process_status": self.event_process_status,
            "event_source": self.event_source,
            "owner": self.owner,
            "event_summary": self.event_summary,
            "log_data": self.log_data,
            "event_ingestion_id": self.event_ingestion_id,
            "room": self.room.id if self.room else None
        }

    class Meta:
        table = "events"
        ordering = ["-utime"]


class EventsIngestion(BaseModel):
    """
    事件接入列表
    """

    event_ingestion_name: str = fields.CharField(max_length=255, null=True, description="事件接入名称")
    event_ingestion_describe: str = fields.CharField(max_length=255, null=True, description="事件接入描述")
    event_source_type: str = fields.CharField(max_length=255, null=True, description="事件来源类型")
    transforms_type: str = fields.CharField(max_length=255, null=True, description="转换类型")
    enabled_status: int = fields.IntField(max_length=50, null=True, description="是否开启上游接入")
    linked_execution: int = fields.IntField(max_length=50, null=True, description="是否开启联动执行")
    creator = fields.ForeignKeyField("models.Account", related_name="created_events_ingestion", description="创建人")
    updator = fields.ForeignKeyField("models.Account", related_name="updated_events_ingestion", description="更新人")
    syslog_config: str = fields.CharField(max_length=255, null=True, description="syslog端口")
    file_path: str = fields.CharField(max_length=255, null=True, description="配置文件路径")
    vector_name: str = fields.CharField(max_length=255, null=True, description="vector名称")
    config_info = fields.JSONField(default={}, null=True, description="转换配置信息")
    vector_config = fields.JSONField(default={}, null=True, description="vector配置信息")
    vector_config_raw = fields.JSONField(default={}, null=True, description="未进行配置剧本接入的vector配置信息")
    enabled_health_status: str = fields.CharField(max_length=255, null=True, description="上游接入健康状态")
    execution_health_status: str = fields.CharField(max_length=255, null=True, description="联动执行健康状态")
    convert_health_status: str = fields.CharField(max_length=255, null=True, description="转换配置健康状态")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "event_ingestion_name": self.event_ingestion_name,
            "event_ingestion_describe": self.event_ingestion_describe,
            "event_source_type": self.event_source_type,
            "transforms_type": self.transforms_type,
            "enabled_status": self.enabled_status,
            "creator": {"username": self.creator.username, "display_name": self.creator.display_name},
            "updator":  {"username": self.updator.username, "display_name": self.updator.display_name},
            "file_path": self.file_path,
            "linked_execution": self.linked_execution,
            "syslog_config": self.syslog_config,
            "vector_name": self.vector_name,
            "enabled_health_status": self.enabled_health_status,
            "execution_health_status": self.execution_health_status,
            "convert_health_status": self.convert_health_status,
            "config_info": self.config_info
        }

    class Meta:
        table = "events_ingestion"
        ordering = ["-utime"]


class EventRelatedScript(BaseModel):
    """
    事件关联剧本表
    """

    condition: str = fields.CharField(max_length=255, null=True, description="执行条件")
    event_ingestion_id: str = fields.CharField(max_length=255, null=True, description="事件接入id")
    topic: str = fields.CharField(max_length=255, null=True, description="主题名")
    script_id: list[str] = fields.JSONField(null=True, description="剧本id")
    event_name: str = fields.CharField(max_length=255, null=True, description="自定义事件名称")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "condition": self.condition,
            "event_ingestion_id": self.event_ingestion_id,
            "topic": self.topic,
            "script_id": self.script_id,
            "event_name": self.event_name
        }

    class Meta:
        table = "event_related_script"
        ordering = ["-utime"]
