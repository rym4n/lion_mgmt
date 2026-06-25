# coding=utf-8
from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


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
    system_instruction = fields.TextField(null=True, description="调用 lion_agent 时使用的 system_instruction 提示词")
    prompt_config = fields.JSONField(default=dict, description="提示词扩展配置")
    is_builtin = fields.BooleanField(default=False, description="是否为内置智能体")
    role = fields.ForeignKeyField(
        "models.DigitalEmployeeRole",
        null=True,
        related_name="employees",
        description="数字员工岗位",
        on_delete=fields.SET_NULL,
    )
    role_key = fields.CharField(max_length=64, null=True, description="数字员工岗位逻辑标识")
    skill_keys = fields.JSONField(default=list, description="启用的数字员工技能 key 列表")
    rbac_role_id = fields.CharField(max_length=36, null=True, description="员工绑定的 RBAC 角色")
    resource_scope = fields.JSONField(default=dict, description="员工资源访问范围")
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
            "system_instruction": self.system_instruction,
            "prompt_config": self.prompt_config,
            "is_builtin": self.is_builtin,
            "role_id": self.role_id,
            "role_key": self.role_key,
            "skill_keys": self.skill_keys,
            "rbac_role_id": self.rbac_role_id,
            "resource_scope": self.resource_scope,
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

    title = fields.CharField(max_length=255, description="任务标题")
    description = fields.TextField(null=True, description="任务描述")
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
            "assigned_agent_id": self.assigned_agent_id,
            "assigned_agent_key": self.assigned_agent_key,
            "dispatch_status": self.dispatch_status,
            "dispatch_reason": self.dispatch_reason,
            "run_id": self.run_id,
            "result": self.result,
            "room_id": self.room_id,
            "creator_id": self.creator_id,
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
