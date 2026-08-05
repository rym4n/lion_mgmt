# coding=utf-8
from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class ReportTemplate(BaseModel):
    """用户可在线编辑的报告模板。"""

    document = fields.ForeignKeyField(
        "models.ReportDocument",
        null=True,
        related_name="source_templates",
        description="关联的 canonical 报告文档",
        on_delete=fields.SET_NULL,
    )
    owner = fields.ForeignKeyField(
        "models.Account",
        null=True,
        related_name="report_templates",
        description="所属用户",
        on_delete=fields.SET_NULL,
    )
    name = fields.CharField(max_length=255, description="模板名称")
    description = fields.TextField(default="", description="模板说明")
    content_html = fields.TextField(default="", description="在线编辑器 HTML 内容")
    fetch_config = fields.JSONField(default=dict, description="取数占位符动作配置")
    annotations = fields.JSONField(default=list, description="文档批注")
    source_template_key = fields.CharField(
        max_length=128,
        null=True,
        description="来源内置模板标识",
    )
    preserve_source_layout = fields.BooleanField(
        default=True,
        description="导入时是否保留原文档版式（页眉页脚、纸张方向等）",
    )
    source_docx_path = fields.CharField(
        max_length=512,
        null=True,
        description="留存的原始 docx 相对路径，用于导出时作为外壳；null 表示未留存",
    )

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "owner_id": self.owner_id,
            "document_id": self.document_id,
            "name": self.name,
            "description": self.description,
            "content_html": self.content_html,
            "fetch_config": self.fetch_config,
            "annotations": self.annotations,
            "source_template_key": self.source_template_key,
            "preserve_source_layout": self.preserve_source_layout,
            "source_docx_path": self.source_docx_path,
        }

    class Meta:
        table = "report_template"
        ordering = ["-utime"]
        indexes = (("tenant_id", "owner_id"),)


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
    template_key = fields.CharField(
        max_length=128, null=True, description="来源模板标识，null 表示空白文档"
    )
    file_path = fields.CharField(
        max_length=512, description="服务器端相对路径（相对于 REPORT_USER_BASE）"
    )
    notify_enabled = fields.BooleanField(default=False, description="是否启用通知")
    notify_config = fields.JSONField(
        null=True, description="通知动作配置，结构同巡检通知配置"
    )
    fetch_config = fields.JSONField(
        null=True, description="取数动作配置，key为占位符前缀，value为动作配置"
    )

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


class ReportSchedule(BaseModel):
    """
    用户报告定时任务表。

    记录汇报助手文档的周期执行配置，并关联一条内部 Timed 记录用于统一定时任务管理。
    """

    document = fields.ForeignKeyField(
        "models.ReportDocument",
        related_name="schedules",
        description="关联报告文档",
        on_delete=fields.CASCADE,
    )
    timed = fields.ForeignKeyField(
        "models.Timed",
        null=True,
        related_name="report_schedules",
        description="关联内部定时任务",
        on_delete=fields.SET_NULL,
    )
    creator = fields.ForeignKeyField(
        "models.Account",
        null=True,
        related_name="report_schedules",
        description="创建人",
        on_delete=fields.SET_NULL,
    )
    name = fields.CharField(max_length=255, description="任务名称")
    cron = fields.CharField(max_length=64, description="cron 表达式")
    export_format = fields.CharField(
        max_length=8, default="docx", description="导出格式 docx/pdf"
    )
    enabled = fields.BooleanField(default=True, description="是否启用")
    last_run_at = fields.DatetimeField(null=True, description="最近执行时间")
    last_status = fields.CharField(
        max_length=16, null=True, description="最近执行状态 ok/failed"
    )
    last_error = fields.TextField(null=True, description="最近执行错误")
    next_run_at = fields.DatetimeField(null=True, description="下次执行时间")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "timed_id": self.timed_id,
            "creator_id": self.creator_id,
            "name": self.name,
            "cron": self.cron,
            "export_format": self.export_format,
            "enabled": self.enabled,
            "last_run_at": strip_timezone_to_seconds(self.last_run_at)
            if self.last_run_at
            else None,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "next_run_at": strip_timezone_to_seconds(self.next_run_at)
            if self.next_run_at
            else None,
        }

    class Meta:
        table = "report_schedule"
        ordering = ["-utime"]
        indexes = (("tenant_id", "document_id"), ("tenant_id", "enabled"))
