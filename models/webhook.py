#!/usr/bin/env python
# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class PlaybookWebhook(BaseModel):
    """
    剧本Webhook模型
    """
    playbook_version = fields.ForeignKeyField("models.PlaybookVersion", related_name="webhooks", description="剧本版本")
    webhook_token = fields.CharField(max_length=16, unique=True, description="webhook唯一标识令牌")
    creator = fields.ForeignKeyField("models.Account", related_name="created_webhooks", description="创建人")
    description = fields.TextField(null=True, description="描述")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "playbook_version_id": self.playbook_version.id,
            "webhook_url": f"/webhook/execute/{self.webhook_token}",
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "creator": {
                "username": self.creator.username,
                "display_name": self.creator.display_name
            } if self.creator else None,
            "description": self.description
        }

    class Meta:
        table = "playbook_webhook"
        ordering = ["-utime"]
