# coding=utf-8
from tortoise import fields

from models.base import BaseModel


class AgentMapping(BaseModel):
    """
    Agent 映射表 - Warroom 与 Agent 的关联
    """

    warroom = fields.OneToOneField("models.Room", related_name="agent", description="关联作战室")
    agent_id = fields.CharField(max_length=64, description="Agent ID")
    status = fields.CharField(max_length=20, default="active", description="状态: active, archived, error")
    archived_at = fields.DatetimeField(null=True)

    class Meta:
        table = "agent_mapping"


class ArchivalKnowledge(BaseModel):
    """
    L5 知识库 - 存储案例经验和 SOP (pgvector)
    """

    content = fields.TextField(description="知识内容文本")
    embedding = fields.JSONField(description="向量 (pgvector 会替换为 vector 类型)", null=True)

    knowledge_type = fields.CharField(max_length=20, description="类型: case_learning, sop, threat_intel")
    attack_type = fields.CharField(max_length=50, null=True, index=True)
    tags = fields.JSONField(default=list, description="标签列表")

    source_warroom_id = fields.CharField(max_length=64, null=True)
    source_event_id = fields.CharField(max_length=64, null=True)
    submitted_by = fields.CharField(max_length=64, null=True, description="提交者: agent/admin")

    review_status = fields.CharField(max_length=20, default="pending", description="审核状态: pending, approved, rejected")
    reviewed_by = fields.ForeignKeyField("models.Account", null=True, related_name="reviewed_knowledge")
    reviewed_at = fields.DatetimeField(null=True)

    class Meta:
        table = "archival_knowledge"
        ordering = ["-ctime"]


class UserPreference(BaseModel):
    """
    L3 用户偏好 - 存储用户个性化配置
    """

    account = fields.OneToOneField("models.Account", related_name="agent_preference")

    notify_channel = fields.CharField(max_length=20, default="in_app", description="通知渠道: in_app, email, slack, webhook")
    notify_config = fields.JSONField(default=dict, description="渠道配置")

    report_language = fields.CharField(max_length=10, default="zh-CN")
    response_verbosity = fields.CharField(max_length=20, default="normal", description="回复详细程度: brief, normal, detailed")

    skill_tags = fields.JSONField(default=list, description="技能标签: malware_analysis, network_forensics, etc.")

    class Meta:
        table = "agent_user_preference"
