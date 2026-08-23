# coding=utf-8
"""知识空间与文档模型（SPEC §7）。

知识空间是 AIOE 自有概念，OpenViking 不感知它——空间到存储的映射
只体现为 URI 前缀 viking://resources/spaces/{space_key}。
隔离由 services/knowledge/scope.py 实现，不依赖 OpenViking 的权限模型。
"""

from models.base import BaseModel
from tortoise import fields

# 空间根前缀。改这个值等于让所有既有文档失联，不要改。
SPACES_URI_PREFIX = "viking://resources/spaces"


class KnowledgeSpace(BaseModel):
    """租户级知识空间：知识内容的组织与授权单元。"""

    space_key = fields.CharField(max_length=64, description="空间标识，租户内唯一，用于 URI")
    name = fields.CharField(max_length=128, description="显示名")
    description = fields.TextField(
        null=True, description="用途说明；注入 Agent 系统提示，作为选择空间的依据"
    )
    source_type = fields.CharField(
        max_length=20, default="manual", description="来源: manual | skill_package"
    )
    source_ref = fields.CharField(
        max_length=128, null=True, description="source_type=skill_package 时为 skill_key"
    )

    @property
    def root_uri(self) -> str:
        return f"{SPACES_URI_PREFIX}/{self.space_key}"

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "space_key": self.space_key,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "ctime": self.ctime,
            "utime": self.utime,
        }

    class Meta:
        table = "knowledge_space"
        unique_together = (("tenant_id", "space_key"),)
        ordering = ["-utime"]


class KnowledgeDocument(BaseModel):
    """知识文档元数据与索引状态。

    一行对应 OpenViking 里的一个文档。lion_mgmt 是唯一写入口，
    所以本表与 OpenViking 内容保持一致；异常由 space/sync 对账修复。

    这张表同时是 URI ↔ document_id 的映射——对外接口只暴露 document_id，
    viking:// URI 不出现在任何响应里。
    """

    space = fields.ForeignKeyField(
        "models.KnowledgeSpace", related_name="documents", on_delete=fields.CASCADE
    )
    space_key = fields.CharField(max_length=64, description="冗余存储，便于按空间查询")
    rel_path = fields.CharField(max_length=512, description="空间内相对路径")
    uri = fields.CharField(max_length=1024, description="完整 viking:// URI，内部使用")
    title = fields.CharField(max_length=255, description="显示名，默认取文件名")
    mime_type = fields.CharField(max_length=128, null=True)
    size = fields.BigIntField(default=0, description="字节数")
    content_hash = fields.CharField(max_length=64, description="SHA256，用于幂等判重")

    source = fields.CharField(
        max_length=20,
        default="manual",
        description="来源: manual | api | skill_package | agent | external",
    )
    source_ref = fields.CharField(
        max_length=255, null=True, description="skill_key / 剧本 run_id / 员工 id"
    )
    uploader_id = fields.CharField(max_length=36, null=True, description="Account.id")

    status = fields.CharField(
        max_length=20, default="pending", description="pending | processing | ready | failed"
    )
    task_id = fields.CharField(max_length=128, null=True, description="OpenViking 异步任务 id")
    error = fields.TextField(null=True, description="失败原因，截断 1000 字符")
    indexed_at = fields.DatetimeField(null=True, description="索引完成时间")

    @property
    def json(self) -> dict:
        """对外序列化。注意：不含 uri——对外只暴露 document_id。"""
        return {
            "id": self.id,
            "space_key": self.space_key,
            "rel_path": self.rel_path,
            "title": self.title,
            "mime_type": self.mime_type,
            "size": self.size,
            "source": self.source,
            "source_ref": self.source_ref,
            "uploader_id": self.uploader_id,
            "status": self.status,
            "error": self.error,
            "indexed_at": self.indexed_at,
            "ctime": self.ctime,
            "utime": self.utime,
        }

    class Meta:
        table = "knowledge_document"
        unique_together = (("tenant_id", "uri"),)
        indexes = (("tenant_id", "space_key", "status"),)
        ordering = ["-utime"]
