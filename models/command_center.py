# coding=utf-8
from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


COMMAND_TASK_STATUS_DRAFT = "draft"
COMMAND_TASK_STATUS_PENDING_DISPATCH = "pending_dispatch"
COMMAND_TASK_STATUS_PENDING_CLARIFICATION = "pending_clarification"
COMMAND_TASK_STATUS_DISPATCHED = "dispatched"
COMMAND_TASK_STATUS_PENDING_APPROVAL = "pending_approval"
COMMAND_TASK_STATUS_RUNNING = "running"
COMMAND_TASK_STATUS_COMPLETED = "completed"
COMMAND_TASK_STATUS_FAILED = "failed"
COMMAND_TASK_STATUS_CANCELLED = "cancelled"
COMMAND_TASK_STATUS_HANDED_OFF = "handed_off"

COMMAND_TASK_TERMINAL_STATUSES = {
    COMMAND_TASK_STATUS_COMPLETED,
    COMMAND_TASK_STATUS_FAILED,
    COMMAND_TASK_STATUS_CANCELLED,
    COMMAND_TASK_STATUS_HANDED_OFF,
}

COMMAND_RUN_STATUS_CREATED = "created"
COMMAND_RUN_STATUS_CHECKED_OUT = "checked_out"
COMMAND_RUN_STATUS_RUNNING = "running"
COMMAND_RUN_STATUS_WAITING_APPROVAL = "waiting_approval"
COMMAND_RUN_STATUS_CONTINUATION_READY = "continuation_ready"
COMMAND_RUN_STATUS_COMPLETED = "completed"
COMMAND_RUN_STATUS_FAILED = "failed"
COMMAND_RUN_STATUS_CANCELLED = "cancelled"
COMMAND_RUN_STATUS_REJECTED = "rejected"
COMMAND_RUN_STATUS_STALE = "stale"
COMMAND_RUN_STATUS_HANDED_OFF = "handed_off"

COMMAND_RUN_TERMINAL_STATUSES = {
    COMMAND_RUN_STATUS_COMPLETED,
    COMMAND_RUN_STATUS_FAILED,
    COMMAND_RUN_STATUS_CANCELLED,
    COMMAND_RUN_STATUS_REJECTED,
    COMMAND_RUN_STATUS_STALE,
    COMMAND_RUN_STATUS_HANDED_OFF,
}

COMMAND_APPROVAL_STATUS_PENDING = "pending"
COMMAND_APPROVAL_STATUS_APPROVED = "approved"
COMMAND_APPROVAL_STATUS_REJECTED = "rejected"
COMMAND_APPROVAL_STATUS_NEEDS_INFO = "needs_info"
COMMAND_APPROVAL_STATUS_APPROVED_READ_ONLY = "approved_read_only"
COMMAND_APPROVAL_STATUS_EXPIRED = "expired"
COMMAND_APPROVAL_STATUS_HANDED_OFF = "handed_off"

COMMAND_RUNTIME_LION_AGENT = "lion_agent"


def _relation_id(instance, name: str) -> str | None:
    return getattr(instance, f"{name}_id", None)


class CommandAgent(BaseModel):
    """
    指挥部智能体登记表。

    这里只保存智能体画像、能力与工作状态，真实智能体执行器后续再接入。
    """

    agent_key = fields.CharField(max_length=64, description="智能体逻辑标识")
    name = fields.CharField(max_length=255, description="智能体名称")
    description = fields.TextField(null=True, description="智能体说明")
    capabilities = fields.JSONField(default=list, description="能力标签列表")
    status = fields.CharField(max_length=20, default="idle", description="状态: idle,busy,offline,error")
    load = fields.IntField(default=0, description="当前任务负载")
    max_concurrent = fields.IntField(default=1, description="最大并发任务数")
    current_task_id = fields.CharField(max_length=36, null=True, description="当前主任务ID")
    heartbeat_at = fields.DatetimeField(null=True, description="最近心跳时间")
    metadata = fields.JSONField(default=dict, description="扩展元数据")
    runtime = fields.CharField(max_length=30, default="lion_agent", description="运行时类型: lion_agent 等")
    prompt_config = fields.JSONField(default=dict, description="提示词扩展配置")
    is_builtin = fields.BooleanField(default=False, description="是否为内置智能体")
    enabled = fields.BooleanField(default=True, description="是否启用")
    role = fields.ForeignKeyField(
        "models.DigitalEmployeeRole",
        null=True,
        related_name="employees",
        description="数字员工岗位",
        on_delete=fields.SET_NULL,
    )
    role_key = fields.CharField(max_length=64, null=True, description="数字员工岗位逻辑标识")
    skill_keys = fields.JSONField(default=list, description="启用的数字员工技能 key 列表")
    knowledge_space_keys = fields.JSONField(
        default=list, description="可见知识空间 key 列表"
    )
    knowledge_write_space = fields.CharField(
        max_length=64, null=True, description="员工写入的目标空间 key，为空则不给写入工具"
    )
    rbac_role_id = fields.CharField(max_length=36, null=True, description="员工绑定的 RBAC 角色")
    resource_scope = fields.JSONField(default=dict, description="员工资源访问范围")
    llm_config = fields.ForeignKeyField(
        "models.LlmModelConfig",
        null=True,
        on_delete=fields.SET_NULL,
        related_name="agents",
        description="指定模型配置，为空则用全局默认",
    )
    workspace_synced_at = fields.DatetimeField(null=True, description="上次同步到 lion_agent 的时间")
    workspace_etag = fields.CharField(max_length=64, null=True, description="workspace 同步版本指纹")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "agent_key": self.agent_key,
            "name": self.name,
            "description": self.description,
            "capabilities": self.capabilities,
            "status": self.status,
            "load": self.load,
            "max_concurrent": self.max_concurrent,
            "current_task_id": self.current_task_id,
            "heartbeat_at": strip_timezone_to_seconds(self.heartbeat_at),
            "metadata": self.metadata,
            "runtime": self.runtime,
            "prompt_config": self.prompt_config,
            "is_builtin": self.is_builtin,
            "enabled": self.enabled,
            "role_id": self.role_id,
            "role_key": self.role_key,
            "skill_keys": self.skill_keys,
            "knowledge_space_keys": self.knowledge_space_keys,
            "knowledge_write_space": self.knowledge_write_space,
            "rbac_role_id": self.rbac_role_id,
            "resource_scope": self.resource_scope,
            "llm_config_id": self.llm_config_id,
            "workspace_synced_at": strip_timezone_to_seconds(self.workspace_synced_at),
            "workspace_etag": self.workspace_etag,
        }

    class Meta:
        table = "command_center_agent"
        ordering = ["-utime"]
        unique_together = (("tenant_id", "agent_key"),)


class CommandTask(BaseModel):
    """
    指挥部任务表。

    支持用户指定智能体或不指定智能体；调度框架会基于能力标签选择或纠正分配。
    """

    title = fields.CharField(max_length=255, description="兼容字段，保存任务内容前255字")
    description = fields.TextField(null=True, description="完整任务内容")
    source = fields.CharField(max_length=50, default="manual", description="任务来源")
    priority = fields.IntField(default=3, description="优先级，数值越小优先级越高")
    status = fields.CharField(max_length=30, default="pending_dispatch", description="任务状态")
    progress = fields.IntField(default=0, description="任务进度 0-100")

    required_capabilities = fields.JSONField(default=list, description="任务所需能力标签")
    payload = fields.JSONField(default=dict, description="任务载荷")

    requested_agent_id = fields.CharField(max_length=64, null=True, description="用户指定的智能体ID或agent_key")
    assigned_agent = fields.ForeignKeyField(
        "models.CommandAgent",
        null=True,
        related_name="assigned_tasks",
        description="实际分配的智能体",
        on_delete=fields.SET_NULL,
    )
    assigned_agent_key = fields.CharField(max_length=64, null=True, description="实际分配的智能体逻辑标识")
    dispatch_status = fields.CharField(max_length=30, default="pending", description="调度状态")
    dispatch_reason = fields.TextField(null=True, description="调度原因")

    run_id = fields.CharField(max_length=36, null=True, description="后续执行系统run_id")
    result = fields.TextField(null=True, description="执行结果")
    room = fields.ForeignKeyField(
        "models.Room",
        null=True,
        related_name="command_tasks",
        description="关联作战室",
        on_delete=fields.SET_NULL,
    )
    creator = fields.ForeignKeyField(
        "models.Account",
        null=True,
        related_name="created_command_tasks",
        description="创建人",
        on_delete=fields.SET_NULL,
    )
    started_at = fields.DatetimeField(null=True, description="开始时间")
    finished_at = fields.DatetimeField(null=True, description="结束时间")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "priority": self.priority,
            "status": self.status,
            "progress": self.progress,
            "required_capabilities": self.required_capabilities,
            "payload": self.payload,
            "requested_agent_id": self.requested_agent_id,
            "assigned_agent_id": _relation_id(self, "assigned_agent"),
            "assigned_agent_key": self.assigned_agent_key,
            "dispatch_status": self.dispatch_status,
            "dispatch_reason": self.dispatch_reason,
            "run_id": self.run_id,
            "result": self.result,
            "room_id": _relation_id(self, "room"),
            "creator_id": _relation_id(self, "creator"),
            "started_at": strip_timezone_to_seconds(self.started_at),
            "finished_at": strip_timezone_to_seconds(self.finished_at),
        }

    class Meta:
        table = "command_center_task"
        ordering = ["priority", "-ctime"]
        indexes = (("tenant_id", "status"), ("tenant_id", "assigned_agent_key"), ("tenant_id", "dispatch_status"))


class CommandTaskEvent(BaseModel):
    """
    指挥部任务事件流，用于展示任务进展与调度历史。
    """

    task = fields.ForeignKeyField("models.CommandTask", related_name="events", description="任务")
    event_type = fields.CharField(max_length=50, description="事件类型")
    status = fields.CharField(max_length=30, null=True, description="任务状态快照")
    progress = fields.IntField(null=True, description="进度快照")
    agent_key = fields.CharField(max_length=64, null=True, description="相关智能体")
    message = fields.TextField(null=True, description="事件说明")
    metadata = fields.JSONField(default=dict, description="事件元数据")
    operator_id = fields.CharField(max_length=36, null=True, description="操作者ID")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "task_id": self.task_id,
            "event_type": self.event_type,
            "status": self.status,
            "progress": self.progress,
            "agent_key": self.agent_key,
            "message": self.message,
            "metadata": self.metadata,
            "operator_id": self.operator_id,
        }

    class Meta:
        table = "command_center_task_event"
        ordering = ["ctime"]


class CommandTaskRun(BaseModel):
    """
    指挥中心任务执行记录。

    每次执行尝试一条记录，任务表只保存当前摘要字段。
    """

    task = fields.ForeignKeyField("models.CommandTask", related_name="runs", description="业务任务", on_delete=fields.CASCADE)
    employee = fields.ForeignKeyField(
        "models.CommandAgent",
        null=True,
        related_name="task_runs",
        description="执行数字员工",
        on_delete=fields.SET_NULL,
    )
    run_id = fields.CharField(max_length=64, description="运行ID")
    session_id = fields.CharField(max_length=128, null=True, description="运行会话ID")
    runtime = fields.CharField(max_length=30, default=COMMAND_RUNTIME_LION_AGENT, description="运行时")
    status = fields.CharField(max_length=30, default=COMMAND_RUN_STATUS_CREATED, description="运行状态")
    lease_owner = fields.CharField(max_length=128, null=True, description="租约持有者")
    lease_expires_at = fields.DatetimeField(null=True, description="租约过期时间")
    heartbeat_at = fields.DatetimeField(null=True, description="最近心跳时间")
    workspace_etag = fields.CharField(max_length=64, null=True, description="workspace 同步版本指纹")
    manifest_snapshot = fields.JSONField(default=dict, description="manifest.json 快照")
    policy_snapshot = fields.JSONField(default=dict, description="policy.json 快照")
    tool_calls = fields.JSONField(default=list, description="工具调用记录")
    resource_accesses = fields.JSONField(default=list, description="资源访问记录")
    usage = fields.JSONField(default=dict, description="用量统计")
    cost = fields.JSONField(default=dict, description="成本统计")
    transcript_ref = fields.CharField(max_length=255, null=True, description="转录/对话引用")
    evidence_refs = fields.JSONField(default=list, description="证据引用")
    error = fields.JSONField(default=dict, description="错误信息")
    started_at = fields.DatetimeField(null=True, description="开始时间")
    finished_at = fields.DatetimeField(null=True, description="结束时间")
    metadata = fields.JSONField(default=dict, description="扩展元数据")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "task_id": _relation_id(self, "task"),
            "employee_id": _relation_id(self, "employee"),
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "runtime": self.runtime,
            "status": self.status,
            "lease_owner": self.lease_owner,
            "lease_expires_at": strip_timezone_to_seconds(self.lease_expires_at),
            "heartbeat_at": strip_timezone_to_seconds(self.heartbeat_at),
            "workspace_etag": self.workspace_etag,
            "manifest_snapshot": self.manifest_snapshot,
            "policy_snapshot": self.policy_snapshot,
            "tool_calls": self.tool_calls,
            "resource_accesses": self.resource_accesses,
            "usage": self.usage,
            "cost": self.cost,
            "transcript_ref": self.transcript_ref,
            "evidence_refs": self.evidence_refs,
            "error": self.error,
            "started_at": strip_timezone_to_seconds(self.started_at),
            "finished_at": strip_timezone_to_seconds(self.finished_at),
            "metadata": self.metadata,
        }

    class Meta:
        table = "command_center_task_run"
        ordering = ["-ctime"]
        indexes = (
            ("tenant_id", "task_id"),
            ("tenant_id", "status"),
            ("tenant_id", "employee_id", "status"),
            ("tenant_id", "heartbeat_at"),
        )
        unique_together = (("tenant_id", "run_id"),)


class CommandApproval(BaseModel):
    """
    指挥中心审批记录。

    该表保存 Agent 审批状态，并由指挥中心提供待办、提醒和统一审批入口。
    """

    task = fields.ForeignKeyField("models.CommandTask", related_name="approvals", description="业务任务", on_delete=fields.CASCADE)
    run = fields.ForeignKeyField(
        "models.CommandTaskRun",
        null=True,
        related_name="approvals",
        description="关联运行",
        on_delete=fields.SET_NULL,
    )
    employee = fields.ForeignKeyField(
        "models.CommandAgent",
        null=True,
        related_name="approvals",
        description="执行数字员工",
        on_delete=fields.SET_NULL,
    )
    status = fields.CharField(max_length=30, default=COMMAND_APPROVAL_STATUS_PENDING, description="审批状态")
    trigger_type = fields.CharField(max_length=50, description="触发类型")
    risk_level = fields.CharField(max_length=20, default="medium", description="风险等级")
    policy_snapshot = fields.JSONField(default=dict, description="policy.json 快照")
    impacted_resources = fields.JSONField(default=list, description="影响资源")
    requested_action = fields.JSONField(default=dict, description="请求动作")
    rollback_plan = fields.TextField(null=True, description="回滚/缓解方案")
    requester_id = fields.CharField(max_length=36, null=True, description="请求人")
    approver_id = fields.CharField(max_length=36, null=True, description="审批人")
    decided_at = fields.DatetimeField(null=True, description="审批时间")
    expires_at = fields.DatetimeField(null=True, description="过期时间")
    decision_comment = fields.TextField(null=True, description="审批意见")
    metadata = fields.JSONField(default=dict, description="扩展元数据")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "task_id": _relation_id(self, "task"),
            "run_id": _relation_id(self, "run"),
            "employee_id": _relation_id(self, "employee"),
            "tenant_id": self.tenant_id,
            "status": self.status,
            "trigger_type": self.trigger_type,
            "risk_level": self.risk_level,
            "policy_snapshot": self.policy_snapshot,
            "impacted_resources": self.impacted_resources,
            "requested_action": self.requested_action,
            "rollback_plan": self.rollback_plan,
            "requester_id": self.requester_id,
            "approver_id": self.approver_id,
            "decided_at": strip_timezone_to_seconds(self.decided_at),
            "expires_at": strip_timezone_to_seconds(self.expires_at),
            "decision_comment": self.decision_comment,
            "metadata": self.metadata,
        }

    class Meta:
        table = "command_center_approval"
        ordering = ["-ctime"]
        indexes = (
            ("tenant_id", "status"),
            ("tenant_id", "task_id"),
            ("tenant_id", "approver_id", "status"),
        )
