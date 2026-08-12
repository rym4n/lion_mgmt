# coding=utf-8
from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel
from tortoise import fields


class EmployeeReport(BaseModel):
    """数字员工任务级报告。"""

    task = fields.ForeignKeyField(
        "models.CommandTask",
        related_name="employee_reports",
        description="关联任务",
        on_delete=fields.CASCADE,
    )
    employee = fields.ForeignKeyField(
        "models.CommandAgent",
        null=True,
        related_name="employee_reports",
        description="产出员工",
        on_delete=fields.SET_NULL,
    )
    role_key = fields.CharField(max_length=64, description="产出岗位")
    title = fields.CharField(max_length=255, description="报告标题")
    severity = fields.CharField(max_length=20, default="info", description="严重级别")
    observation = fields.TextField(null=True, description="观察")
    analysis = fields.TextField(null=True, description="分析")
    actions = fields.JSONField(default=list, description="执行动作摘要")
    conclusion = fields.TextField(null=True, description="结论")
    evidence_urls = fields.JSONField(default=list, description="证据链接")
    report_document_id = fields.CharField(max_length=36, null=True, description="关联 ReportDocument")
    auto_generated = fields.BooleanField(default=True, description="是否自动生成")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "employee_id": self.employee_id,
            "role_key": self.role_key,
            "title": self.title,
            "severity": self.severity,
            "observation": self.observation,
            "analysis": self.analysis,
            "actions": self.actions,
            "conclusion": self.conclusion,
            "evidence_urls": self.evidence_urls,
            "report_document_id": self.report_document_id,
            "auto_generated": self.auto_generated,
        }

    class Meta:
        table = "digital_employee_report"
        ordering = ["-ctime"]
        indexes = (("tenant_id", "role_key"), ("tenant_id", "task_id"), ("tenant_id", "severity"))
