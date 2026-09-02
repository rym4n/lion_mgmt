# coding=utf-8
"""知识空间与文档模型（SPEC §4）。

存储后端是 Qdrant（向量）+ RustFS（原文与派生文本），经 lion_agent 访问。
本表是文档元数据与状态的唯一真相，document_id 同时是 Qdrant 里的
document_id（SPEC D2），因此不需要任何映射字段。
"""

from models.base import BaseModel
from tortoise import fields


class KnowledgeSpace(BaseModel):
    """租户级知识空间：知识内容的组织与授权单元。"""

    space_key = fields.CharField(max_length=64, description="空间标识，租户内唯一")
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

    本行的 id 直接作为 Qdrant 的 document_id 使用（SPEC D2），
    删除文档时用同一个 id 调 lion_agent 移除向量记录。
    """

    space = fields.ForeignKeyField(
        "models.KnowledgeSpace", related_name="documents", on_delete=fields.CASCADE
    )
    space_key = fields.CharField(max_length=64, description="冗余存储，便于按空间查询")
    title = fields.CharField(max_length=255, description="显示名，默认取文件名")
    mime_type = fields.CharField(max_length=128, null=True)
    size = fields.BigIntField(default=0, description="字节数")
    content_hash = fields.CharField(max_length=64, description="SHA256，用于幂等判重")
    storage_key = fields.CharField(
        max_length=512, default="", description="RustFS 上原始字节的对象键（内部使用）"
    )

    source = fields.CharField(
        max_length=20,
        default="manual",
        description="来源: manual | api | skill_package | agent",
    )
    source_ref = fields.CharField(
        max_length=255, null=True, description="skill_key / 剧本 run_id / 伙伴 id"
    )
    uploader_id = fields.CharField(max_length=36, null=True, description="Account.id")

    status = fields.CharField(
        max_length=20, default="processing", description="processing | ready | failed"
    )
    error = fields.TextField(null=True, description="失败原因，截断 1000 字符")
    indexed_at = fields.DatetimeField(null=True, description="索引完成时间")

    @property
    def json(self) -> dict:
        """对外序列化。不含 storage_key——那是内部存储坐标。"""
        return {
            "id": self.id,
            "space_key": self.space_key,
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
        unique_together = (("tenant_id", "space_key", "content_hash"),)
        indexes = (("tenant_id", "space_key", "status"),)
        ordering = ["-utime"]
