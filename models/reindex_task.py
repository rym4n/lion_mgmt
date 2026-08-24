# coding=utf-8
from tortoise import fields
from models.base import BaseModel


class ReindexTask(BaseModel):
    """Reindex 任务（记录每次维度变化后的重建进度）"""

    class Meta:
        table = "reindex_task"

    # 关联的配置变更
    config_id = fields.CharField(max_length=36, index=True)  # EmbeddingConfig.id
    tenant_id = fields.CharField(max_length=36, index=True)

    # 任务状态
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    status = fields.CharField(
        max_length=20,
        default=STATUS_PENDING,
        index=True,
    )

    # 进度统计
    total_workspaces = fields.IntField(default=0)
    processed_workspaces = fields.IntField(default=0)
    failed_workspaces = fields.IntField(default=0)

    # 详细进度（JSON 数组，每个元素 {"workspace_id": "...", "status": "success|failed", "error": "..."}）
    details = fields.JSONField(default=list)

    # 时间戳
    started_at = fields.DatetimeField(null=True)
    completed_at = fields.DatetimeField(null=True)

    # 错误信息（任务级）
    error = fields.TextField(null=True, blank=True)

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "config_id": self.config_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "total_workspaces": self.total_workspaces,
            "processed_workspaces": self.processed_workspaces,
            "failed_workspaces": self.failed_workspaces,
            "details": self.details,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }
