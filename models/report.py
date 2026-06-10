# coding=utf-8
from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class ReportDocument(BaseModel):
    """
    用户报告文档表。

    记录用户基于模板另存的报告文件，便于后续重新编辑时找回。
    """

    owner = fields.ForeignKeyField(
        "models.Account",
        null=True,
        related_name="report_documents",
        description="所属用户",
        on_delete=fields.SET_NULL,
    )
    name = fields.CharField(max_length=255, description="文件名")
    template_key = fields.CharField(max_length=128, null=True, description="来源模板标识，null 表示空白文档")
    file_path = fields.CharField(max_length=512, description="服务器端相对路径（相对于 REPORT_USER_BASE）")
    notify_enabled = fields.BooleanField(default=False, description="是否启用通知")
    notify_config = fields.JSONField(null=True, description="通知动作配置，结构同巡检通知配置")
    fetch_config = fields.JSONField(null=True, description="取数动作配置，key为占位符前缀，value为动作配置")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "owner_id": self.owner_id,
            "name": self.name,
            "template_key": self.template_key,
            "file_path": self.file_path,
            "notify_enabled": self.notify_enabled,
            "notify_config": self.notify_config,
            "fetch_config": self.fetch_config,
        }

    class Meta:
        table = "report_document"
        ordering = ["-utime"]
        indexes = (("tenant_id", "owner_id"),)
