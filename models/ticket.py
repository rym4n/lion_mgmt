# coding=utf-8
"""工单管理数据模型。九张表 + 状态枚举 + 状态转移表。

状态与转移规则出自 SPEC §3.1/§4.8，引擎与测试都以此处常量为唯一口径。
"""

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel

# 工单状态
TICKET_STATUS_PROCESSING = 0
TICKET_STATUS_COMPLETED = 1
TICKET_STATUS_REJECTED = 2
TICKET_STATUS_TIMEOUT = 3
TICKET_STATUS_WITHDRAWN = 4
TICKET_STATUS_FORCE_CLOSED = 5
TICKET_STATUS_SUSPENDED = 6

TICKET_TERMINAL_STATUSES = frozenset({
    TICKET_STATUS_COMPLETED, TICKET_STATUS_REJECTED, TICKET_STATUS_TIMEOUT,
    TICKET_STATUS_WITHDRAWN, TICKET_STATUS_FORCE_CLOSED,
})

# 工单状态转移表（SPEC §4.8）。终态映射到空集合，任何变更都拒绝。
TICKET_TRANSITIONS = {
    TICKET_STATUS_PROCESSING: frozenset({1, 2, 3, 4, 5, 6}),
    TICKET_STATUS_SUSPENDED: frozenset({TICKET_STATUS_PROCESSING, TICKET_STATUS_FORCE_CLOSED}),
    TICKET_STATUS_COMPLETED: frozenset(),
    TICKET_STATUS_REJECTED: frozenset(),
    TICKET_STATUS_TIMEOUT: frozenset(),
    TICKET_STATUS_WITHDRAWN: frozenset(),
    TICKET_STATUS_FORCE_CLOSED: frozenset(),
}

# 节点状态
NODE_STATUS_PENDING = 0
NODE_STATUS_APPROVED = 1
NODE_STATUS_REJECTED = 2
NODE_STATUS_TIMEOUT = 3
NODE_STATUS_RUNNING = 4
NODE_STATUS_ABNORMAL = 5

NODE_TERMINAL_STATUSES = frozenset({
    NODE_STATUS_APPROVED, NODE_STATUS_REJECTED, NODE_STATUS_TIMEOUT,
})

NODE_TRANSITIONS = {
    NODE_STATUS_PENDING: frozenset({1, 2, 3, 5}),
    NODE_STATUS_RUNNING: frozenset({1, 2, 5}),
    NODE_STATUS_ABNORMAL: frozenset({NODE_STATUS_PENDING}),
    NODE_STATUS_APPROVED: frozenset(),
    NODE_STATUS_REJECTED: frozenset(),
    NODE_STATUS_TIMEOUT: frozenset(),
}

# 节点类型
NODE_TYPE_START = "start"
NODE_TYPE_APPROVE = "approve"
NODE_TYPE_AUTO = "auto"
NODE_TYPE_CONDITION = "condition"
NODE_TYPE_PARALLEL = "parallel"
NODE_TYPE_MERGE = "merge"
NODE_TYPE_END = "end"

# 会签/或签
APPROVE_TYPE_ANY = 1
APPROVE_TYPE_ALL = 2

# 工单来源
SOURCE_MANUAL = 0
SOURCE_PLAYBOOK = 1
SOURCE_AGENT = 2
SOURCE_EVENT = 3

# 汇聚策略
MERGE_STRATEGY_ALL = "all"
MERGE_STRATEGY_ANY = "any"

# 落败支路取消标记，写在 node.results 里（不占用状态枚举，SPEC §4.2）
RESULT_KEY_CANCELLED = "__cancelled__"


class TicketType(BaseModel):
    """工单类型。内置（事件/问题/变更/请求/故障）走包导入，租户可自定义。"""

    name: str = fields.CharField(max_length=64, description="类型名称")
    code: str = fields.CharField(max_length=32, index=True, description="类型编码，用于单号前缀，如 CHG")
    description: str = fields.CharField(max_length=512, null=True, description="描述")
    icon: str = fields.CharField(max_length=128, null=True, description="图标标识")
    internal: bool = fields.BooleanField(default=False, description="是否系统内置")
    default_flow = fields.ForeignKeyField(
        "models.TicketFlow", related_name="default_for_types", null=True,
        on_delete=fields.SET_NULL, description="默认流程",
    )
    sla_config = fields.JSONField(
        default=dict,
        description='按优先级配置时限：{"3": {"respond": 900, "resolve": 7200}, "warn_before": 900}',
    )

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "icon": self.icon,
            "internal": self.internal,
            "default_flow_id": self.default_flow_id,
            "sla_config": self.sla_config or {},
        }

    class Meta:
        table = "ticket_type"
        unique_together = (("tenant_id", "code"),)
        ordering = ["-utime"]


class TicketFlow(BaseModel):
    """流程定义，对应 Playbook。版本内容在 TicketFlowVersion。"""

    name: str = fields.CharField(max_length=128, description="流程名称")
    remark: str = fields.CharField(max_length=512, null=True, description="备注")
    internal: bool = fields.BooleanField(default=False, description="是否系统内置")
    ticket_type = fields.ForeignKeyField(
        "models.TicketType", related_name="flows", description="所属工单类型",
    )
    creator = fields.ForeignKeyField(
        "models.Account", related_name="created_ticket_flows", null=True,
        on_delete=fields.SET_NULL, description="创建人",
    )

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "remark": self.remark,
            "internal": self.internal,
            "ticket_type_id": self.ticket_type_id,
            "creator_id": self.creator_id,
        }

    class Meta:
        table = "ticket_flow"
        ordering = ["-utime"]


class TicketFlowVersion(BaseModel):
    """流程版本，草稿/发布双态。工单创建时钉住某个已发布版本。"""

    ticket_flow = fields.ForeignKeyField(
        "models.TicketFlow", related_name="versions", description="所属流程",
    )
    version: str = fields.CharField(max_length=32, description="版本号")
    change_desc: str = fields.CharField(max_length=512, null=True, description="变更说明")
    status = fields.IntField(default=0, index=True, description="0草稿 1已发布")
    flow_json = fields.JSONField(default=dict, description="nodes + edges，节点内含节点表单/SLA/自动动作配置")
    form_schema = fields.JSONField(default=dict, description="提单表单定义")
    creator = fields.ForeignKeyField(
        "models.Account", related_name="created_ticket_flow_versions", null=True,
        on_delete=fields.SET_NULL,
    )
    updator = fields.ForeignKeyField(
        "models.Account", related_name="updated_ticket_flow_versions", null=True,
        on_delete=fields.SET_NULL,
    )

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "ticket_flow_id": self.ticket_flow_id,
            "version": self.version,
            "change_desc": self.change_desc,
            "status": self.status,
            "flow_json": self.flow_json or {},
            "form_schema": self.form_schema or {},
        }

    class Meta:
        table = "ticket_flow_version"
        ordering = ["-ctime"]


class TicketFlowPermission(BaseModel):
    """流程级权限。SPEC §3.1：flow 仅普通索引，不加 unique（避免复制
    PlaybookPermissions.unique=True 与连创三行的既有矛盾）。"""

    flow = fields.ForeignKeyField(
        "models.TicketFlow", related_name="permissions", index=True, description="所属流程",
    )
    users = fields.ForeignKeyField(
        "models.Account", related_name="ticket_flow_permissions", null=True,
        on_delete=fields.SET_NULL, description="授权用户",
    )
    roles = fields.ForeignKeyField(
        "models.Role", related_name="ticket_flow_permissions", null=True,
        on_delete=fields.SET_NULL, description="授权角色",
    )
    permissions = fields.JSONField(default=list, description='["r","u","d","s"]，s=提单')

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "flow_id": self.flow_id,
            "users_id": self.users_id,
            "roles_id": self.roles_id,
            "permissions": self.permissions or [],
        }

    class Meta:
        table = "ticket_flow_permission"
        ordering = ["-utime"]


class Ticket(BaseModel):
    """工单主表。没有 current_node 字段——并行分支下当前节点是
    status=0 的节点实例集合（SPEC §3.1）。"""

    ticket_no: str = fields.CharField(max_length=64, index=True, description="可读单号，如 CHG-20260810-0001")
    title: str = fields.CharField(max_length=255, description="标题")
    priority = fields.IntField(default=1, index=True, description="0低 1中 2高 3紧急")
    status = fields.IntField(default=TICKET_STATUS_PROCESSING, index=True,
                             description="0处理中 1已完成 2已拒绝 3已超时 4已撤回 5已强关 6异常挂起")
    ticket_type = fields.ForeignKeyField("models.TicketType", related_name="tickets")
    flow_version = fields.ForeignKeyField(
        "models.TicketFlowVersion", related_name="tickets", description="创建时钉住的流程版本",
    )
    creator = fields.ForeignKeyField(
        "models.Account", related_name="created_tickets", null=True,
        on_delete=fields.SET_NULL, description="提单人",
    )
    form_data = fields.JSONField(default=dict, description="提单表单填写值")
    source = fields.IntField(default=SOURCE_MANUAL, index=True, description="0手动 1剧本 2数字伙伴 3事件")

    # 剧本触发场景：终态时用这四个字段重建恢复消息（SPEC §2.1 恢复契约）
    origin_run_id = fields.CharField(max_length=64, null=True, index=True, description="剧本 run_id")
    origin_node_id = fields.CharField(max_length=64, null=True, description="剧本审批节点 id")
    origin_flow_id = fields.CharField(max_length=64, null=True, description="剧本版本 id")
    origin_params = fields.JSONField(null=True, description="剧本侧原始 params，用于重建 flow_input")

    callback_token = fields.CharField(max_length=64, null=True, index=True,
                                      description="外部回调凭证，一次性，用后置 null")

    sla_respond_at = fields.DatetimeField(null=True, description="响应时限，缺配置时为 null")
    sla_resolve_at = fields.DatetimeField(null=True, description="解决时限，缺配置时为 null")
    closed_at = fields.DatetimeField(null=True, description="进入终态的时刻")
    suspended_at = fields.DatetimeField(null=True, description="异常挂起起始时刻，SLA 暂停用")

    # claim/lease：所有 worker 都扫，处理前先 claim 本行（SPEC §4.7）
    sweep_owner = fields.CharField(max_length=128, null=True, description="当前 claim 的 worker 标识")
    sweep_expires_at = fields.DatetimeField(null=True, description="claim 租约到期时间")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "ticket_no": self.ticket_no,
            "title": self.title,
            "priority": self.priority,
            "status": self.status,
            "ticket_type_id": self.ticket_type_id,
            "flow_version_id": self.flow_version_id,
            "creator_id": self.creator_id,
            "form_data": self.form_data or {},
            "source": self.source,
            "sla_respond_at": strip_timezone_to_seconds(self.sla_respond_at) if self.sla_respond_at else None,
            "sla_resolve_at": strip_timezone_to_seconds(self.sla_resolve_at) if self.sla_resolve_at else None,
            "closed_at": strip_timezone_to_seconds(self.closed_at) if self.closed_at else None,
        }

    class Meta:
        table = "ticket"
        unique_together = (("tenant_id", "ticket_no"),)
        ordering = ["-ctime"]


class TicketNodeInstance(BaseModel):
    """节点实例。条件分支未选中的支路不建实例，因此没有「跳过」态。"""

    ticket = fields.ForeignKeyField("models.Ticket", related_name="nodes")
    node_id: str = fields.CharField(max_length=64, index=True, description="flow_json 中的节点 id")
    node_name: str = fields.CharField(max_length=128, null=True, description="节点名称快照")
    node_type: str = fields.CharField(max_length=32, description="见 NODE_TYPE_*")
    status = fields.IntField(default=NODE_STATUS_PENDING, index=True,
                            description="0待处理 1通过 2拒绝 3超时 4执行中 5异常")
    approve_type = fields.IntField(default=APPROVE_TYPE_ANY, description="1或签 2会签")
    approvers = fields.ManyToManyField(
        "models.Account", related_name="ticket_approve_nodes",
        through="ticket_node_approvers", description="当前审批人，变更留痕在 activity",
    )
    results = fields.JSONField(default=dict, description="每人的处理记录 {account_id: {...}}")
    form_data = fields.JSONField(default=dict, description="节点表单填写值")
    deadline = fields.DatetimeField(null=True, index=True, description="节点级 SLA 到期时刻")

    # 汇聚按 fork 分支计数，不按入边（SPEC §4.2 死锁反例）
    fork_id = fields.CharField(max_length=64, null=True, index=True, description="所属并行块的分叉节点 id")
    branch_key = fields.CharField(max_length=64, null=True, description="所属支路标识，每路只贡献一个计数")

    auto_run_id = fields.CharField(max_length=64, null=True, index=True, description="自动节点的动作 run_id")
    entered_at = fields.DatetimeField(null=True, description="激活时刻，节点 deadline 的锚点")
    completed_at = fields.DatetimeField(null=True, description="达终态时刻")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,
            "status": self.status,
            "approve_type": self.approve_type,
            "results": self.results or {},
            "form_data": self.form_data or {},
            "deadline": strip_timezone_to_seconds(self.deadline) if self.deadline else None,
            "entered_at": strip_timezone_to_seconds(self.entered_at) if self.entered_at else None,
            "completed_at": strip_timezone_to_seconds(self.completed_at) if self.completed_at else None,
        }

    class Meta:
        table = "ticket_node_instance"
        ordering = ["ctime"]


class TicketActivity(BaseModel):
    """活动时间线。评论合并进本表，不单独建表——单条时间线天然满足审计。"""

    ticket = fields.ForeignKeyField("models.Ticket", related_name="activities")
    node_instance = fields.ForeignKeyField(
        "models.TicketNodeInstance", related_name="activities", null=True,
        on_delete=fields.SET_NULL,
    )
    actor = fields.ForeignKeyField(
        "models.Account", related_name="ticket_activities", null=True,
        on_delete=fields.SET_NULL, description="操作人，自动节点/系统为 null",
    )
    action: str = fields.CharField(max_length=32, index=True, description="submit/approve/reject/...")
    content = fields.TextField(null=True, description="意见或说明")
    payload = fields.JSONField(default=dict, description="结构化载荷，如审批人变更前后快照")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "ticket_id": self.ticket_id,
            "node_instance_id": self.node_instance_id,
            "actor_id": self.actor_id,
            "action": self.action,
            "content": self.content,
            "payload": self.payload or {},
        }

    class Meta:
        table = "ticket_activity"
        ordering = ["-ctime"]


class TicketAttachment(BaseModel):
    """附件。文件走现有 S3/RustFS（RY_FS_HOST），复用作战室上传逻辑。"""

    ticket = fields.ForeignKeyField("models.Ticket", related_name="attachments")
    activity = fields.ForeignKeyField(
        "models.TicketActivity", related_name="attachments", null=True,
        on_delete=fields.SET_NULL,
    )
    file_name: str = fields.CharField(max_length=255, description="原始文件名")
    file_key: str = fields.CharField(max_length=512, description="对象存储 key")
    file_size = fields.BigIntField(default=0, description="字节数")
    uploader = fields.ForeignKeyField(
        "models.Account", related_name="ticket_attachments", null=True,
        on_delete=fields.SET_NULL,
    )

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "ticket_id": self.ticket_id,
            "file_name": self.file_name,
            "file_key": self.file_key,
            "file_size": self.file_size,
            "uploader_id": self.uploader_id,
        }

    class Meta:
        table = "ticket_attachment"
        ordering = ["-ctime"]


class TicketLink(BaseModel):
    """关联工单。只存一行，查询时双向 OR；不做父子级联、不做状态联动。"""

    from_ticket = fields.ForeignKeyField("models.Ticket", related_name="links_from")
    to_ticket = fields.ForeignKeyField("models.Ticket", related_name="links_to")
    creator = fields.ForeignKeyField(
        "models.Account", related_name="ticket_links", null=True,
        on_delete=fields.SET_NULL,
    )

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "from_ticket_id": self.from_ticket_id,
            "to_ticket_id": self.to_ticket_id,
            "creator_id": self.creator_id,
        }

    class Meta:
        table = "ticket_link"
        ordering = ["-ctime"]
