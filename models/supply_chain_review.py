# coding=utf-8
from __future__ import annotations

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel
from tortoise import fields


CI_TYPE_STATUS_ACTIVE = "active"
CI_TYPE_STATUS_DISABLED = "disabled"
CI_STATUS_ACTIVE = "active"
CI_STATUS_DISABLED = "disabled"
CI_STATUS_ARCHIVED = "archived"
CI_RISK_STATUS_NORMAL = "normal"

FIELD_DATA_TYPES = {"text", "number", "date", "datetime", "enum", "bool", "attachment", "ref"}
RELATION_CARDINALITIES = {"1-1", "1-n", "n-n"}


class SupplyChainCiType(BaseModel):
    """CMDB model definition for supply-chain review assets."""

    key = fields.CharField(max_length=64, description="模型唯一标识")
    name = fields.CharField(max_length=255, description="模型名称")
    icon = fields.CharField(max_length=100, null=True, description="图标")
    color = fields.CharField(max_length=32, null=True, description="颜色")
    category = fields.CharField(max_length=30, default="asset", description="asset/master_data/org")
    primary_field = fields.CharField(max_length=64, default="", description="主展示字段")
    status = fields.CharField(max_length=20, default=CI_TYPE_STATUS_ACTIVE, description="active/disabled")
    is_locked = fields.BooleanField(default=False, description="交付锁定标记")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "key": self.key,
            "name": self.name,
            "icon": self.icon,
            "color": self.color,
            "category": self.category,
            "primary_field": self.primary_field,
            "status": self.status,
            "is_locked": self.is_locked,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_ci_type"
        ordering = ["-utime"]
        unique_together = (("tenant_id", "key"),)
        indexes = (("tenant_id", "status"),)


class SupplyChainCiFieldDef(BaseModel):
    """Dynamic field definition for a CI model."""

    ci_type_id = fields.CharField(max_length=36, description="所属模型 ID")
    ci_type_key = fields.CharField(max_length=64, description="所属模型 key")
    field_key = fields.CharField(max_length=64, description="字段 key")
    label = fields.CharField(max_length=255, description="字段名称")
    data_type = fields.CharField(max_length=20, description="字段类型")
    enum_options = fields.JSONField(default=list, description="枚举选项")
    ref_ci_type_id = fields.CharField(max_length=36, null=True, description="引用模型 ID")
    ref_ci_type = fields.CharField(max_length=64, null=True, description="引用模型 key")
    required = fields.BooleanField(default=False, description="是否必填")
    unique = fields.BooleanField(default=False, description="是否唯一")
    searchable = fields.BooleanField(default=False, description="是否可搜索")
    listable = fields.BooleanField(default=True, description="是否列表展示")
    default_value = fields.JSONField(null=True, description="默认值")
    sort = fields.IntField(default=0, description="排序")
    is_locked = fields.BooleanField(default=False, description="锁定字段")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "ci_type_id": self.ci_type_id,
            "ci_type_key": self.ci_type_key,
            "field_key": self.field_key,
            "label": self.label,
            "data_type": self.data_type,
            "enum_options": self.enum_options or [],
            "ref_ci_type_id": self.ref_ci_type_id,
            "ref_ci_type": self.ref_ci_type,
            "required": self.required,
            "unique": self.unique,
            "searchable": self.searchable,
            "listable": self.listable,
            "default_value": self.default_value,
            "sort": self.sort,
            "is_locked": self.is_locked,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_ci_field_def"
        ordering = ["sort", "ctime"]
        unique_together = (("tenant_id", "ci_type_id", "field_key"),)
        indexes = (("tenant_id", "ci_type_id"), ("tenant_id", "data_type"))


class SupplyChainCiRelationType(BaseModel):
    """Relation type definition between CI models."""

    key = fields.CharField(max_length=64, description="关系 key")
    name = fields.CharField(max_length=255, description="关系名称")
    source_ci_type_ids = fields.JSONField(default=list, description="源模型 ID 列表")
    target_ci_type_ids = fields.JSONField(default=list, description="目标模型 ID 列表")
    source_ci_types = fields.JSONField(default=list, description="源模型 key 列表")
    target_ci_types = fields.JSONField(default=list, description="目标模型 key 列表")
    cardinality = fields.CharField(max_length=10, default="n-n", description="1-1/1-n/n-n")
    relation_fields = fields.JSONField(default=list, description="关系属性字段定义")
    is_locked = fields.BooleanField(default=False, description="交付锁定标记")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "key": self.key,
            "name": self.name,
            "source_ci_type_ids": self.source_ci_type_ids or [],
            "target_ci_type_ids": self.target_ci_type_ids or [],
            "source_ci_types": self.source_ci_types or [],
            "target_ci_types": self.target_ci_types or [],
            "cardinality": self.cardinality,
            "relation_fields": self.relation_fields or [],
            "is_locked": self.is_locked,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_ci_relation_type"
        ordering = ["-utime"]
        unique_together = (("tenant_id", "key"),)


class SupplyChainCi(BaseModel):
    """CMDB instance with dynamic attributes."""

    ci_type_id = fields.CharField(max_length=36, description="模型 ID")
    ci_type_key = fields.CharField(max_length=64, description="模型 key")
    name = fields.CharField(max_length=255, description="实例名称")
    project_area_id = fields.CharField(max_length=36, null=True, description="厂区/区域 CI")
    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    attributes = fields.JSONField(default=dict, description="动态属性")
    status = fields.CharField(max_length=20, default=CI_STATUS_ACTIVE, description="active/disabled/archived")
    risk_status = fields.CharField(max_length=20, default=CI_RISK_STATUS_NORMAL, description="normal/low/medium/high")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "ci_type_id": self.ci_type_id,
            "ci_type_key": self.ci_type_key,
            "name": self.name,
            "project_area_id": self.project_area_id,
            "accessory_project_id": self.accessory_project_id,
            "attributes": self.attributes or {},
            "status": self.status,
            "risk_status": self.risk_status,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_ci"
        ordering = ["-utime"]
        indexes = (
            ("tenant_id", "ci_type_id"),
            ("tenant_id", "ci_type_id", "status"),
            ("tenant_id", "project_area_id"),
            ("tenant_id", "accessory_project_id"),
            ("tenant_id", "ci_type_id", "project_area_id", "accessory_project_id"),
        )


class SupplyChainCiRelation(BaseModel):
    """Relation instance between two CIs."""

    relation_type_key = fields.CharField(max_length=64, description="关系类型 key")
    source_ci = fields.ForeignKeyField("models.SupplyChainCi", related_name="source_relations", description="源 CI", on_delete=fields.CASCADE)
    target_ci = fields.ForeignKeyField("models.SupplyChainCi", related_name="target_relations", description="目标 CI", on_delete=fields.CASCADE)
    attributes = fields.JSONField(default=dict, description="关系属性")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "relation_type_key": self.relation_type_key,
            "source_ci_id": self.source_ci_id,
            "target_ci_id": self.target_ci_id,
            "attributes": self.attributes or {},
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_ci_relation"
        ordering = ["-utime"]
        indexes = (
            ("tenant_id", "relation_type_key"),
            ("tenant_id", "source_ci_id"),
            ("tenant_id", "target_ci_id"),
        )
        unique_together = (("tenant_id", "relation_type_key", "source_ci_id", "target_ci_id"),)


class SupplyChainProjectAuthorization(BaseModel):
    """Personnel project authorization ledger."""

    personnel_ci_id = fields.CharField(max_length=36, description="人员 CI")
    project_area_id = fields.CharField(max_length=36, null=True, description="厂区/区域 CI")
    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    secrecy_level = fields.CharField(max_length=20, default="medium", description="low/medium/high/critical")
    project_role = fields.CharField(max_length=100, null=True, description="项目角色")
    authorized_from = fields.DateField(null=True, description="授权开始日期")
    authorized_to = fields.DateField(null=True, description="授权结束日期")
    access_control_status = fields.CharField(max_length=20, default="normal", description="normal/abnormal/revoked")
    status = fields.CharField(max_length=20, default="active", description="active/expired/revoked")
    remark = fields.TextField(null=True, description="备注")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "personnel_ci_id": self.personnel_ci_id,
            "project_area_id": self.project_area_id,
            "accessory_project_id": self.accessory_project_id,
            "secrecy_level": self.secrecy_level,
            "project_role": self.project_role,
            "authorized_from": str(self.authorized_from) if self.authorized_from else None,
            "authorized_to": str(self.authorized_to) if self.authorized_to else None,
            "access_control_status": self.access_control_status,
            "status": self.status,
            "remark": self.remark,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_project_authorization"
        ordering = ["-utime"]
        indexes = (
            ("tenant_id", "project_area_id"),
            ("tenant_id", "accessory_project_id"),
            ("tenant_id", "personnel_ci_id"),
            ("tenant_id", "status"),
        )


class SupplyChainNdaRecord(BaseModel):
    """Personnel NDA signing ledger."""

    personnel_ci_id = fields.CharField(max_length=36, description="人员 CI")
    project_area_id = fields.CharField(max_length=36, null=True, description="厂区/区域 CI")
    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    nda_type = fields.CharField(max_length=50, null=True, description="NDA 类型")
    sign_status = fields.CharField(max_length=20, default="unsigned", description="signed/unsigned/expired")
    signed_at = fields.DatetimeField(null=True, description="签署时间")
    expires_at = fields.DatetimeField(null=True, description="到期时间")
    evidence_attachment_ids = fields.JSONField(default=list, description="证明附件")
    source_type = fields.CharField(max_length=30, default="manual", description="hr/esign/manual")
    source_record_id = fields.CharField(max_length=100, null=True, description="来源记录")
    last_sync_at = fields.DatetimeField(null=True, description="最近同步时间")
    remark = fields.TextField(null=True, description="备注")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "personnel_ci_id": self.personnel_ci_id,
            "project_area_id": self.project_area_id,
            "accessory_project_id": self.accessory_project_id,
            "nda_type": self.nda_type,
            "sign_status": self.sign_status,
            "signed_at": strip_timezone_to_seconds(self.signed_at),
            "expires_at": strip_timezone_to_seconds(self.expires_at),
            "evidence_attachment_ids": self.evidence_attachment_ids or [],
            "source_type": self.source_type,
            "source_record_id": self.source_record_id,
            "last_sync_at": strip_timezone_to_seconds(self.last_sync_at),
            "remark": self.remark,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_nda_record"
        ordering = ["-utime"]
        indexes = (
            ("tenant_id", "project_area_id"),
            ("tenant_id", "accessory_project_id"),
            ("tenant_id", "personnel_ci_id"),
            ("tenant_id", "sign_status"),
            ("tenant_id", "expires_at"),
        )


class SupplyChainUnknownTerminal(BaseModel):
    """Network-side unknown terminal discovery ledger."""

    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    mac_address = fields.CharField(max_length=64, description="MAC 地址")
    ip_address = fields.CharField(max_length=64, null=True, description="IP 地址")
    network_device_ci_id = fields.CharField(max_length=36, null=True, description="网络设备 CI")
    port = fields.CharField(max_length=100, null=True, description="交换机端口")
    first_seen_at = fields.DatetimeField(null=True, description="首次发现")
    last_seen_at = fields.DatetimeField(null=True, description="最近发现")
    discovery_source = fields.CharField(max_length=30, default="switch_mac", description="switch_mac/arp/scan")
    status = fields.CharField(max_length=20, default="pending", description="pending/confirmed/converted")
    endpoint_ci_id = fields.CharField(max_length=36, null=True, description="终端 CI")
    review_issue_id = fields.CharField(max_length=36, null=True, description="审查问题")
    remark = fields.TextField(null=True, description="备注")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "accessory_project_id": self.accessory_project_id,
            "mac_address": self.mac_address,
            "ip_address": self.ip_address,
            "network_device_ci_id": self.network_device_ci_id,
            "port": self.port,
            "first_seen_at": strip_timezone_to_seconds(self.first_seen_at),
            "last_seen_at": strip_timezone_to_seconds(self.last_seen_at),
            "discovery_source": self.discovery_source,
            "status": self.status,
            "endpoint_ci_id": self.endpoint_ci_id,
            "review_issue_id": self.review_issue_id,
            "remark": self.remark,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_unknown_terminal"
        ordering = ["-utime"]
        indexes = (
            ("tenant_id", "accessory_project_id"),
            ("tenant_id", "mac_address"),
            ("tenant_id", "status"),
            ("tenant_id", "last_seen_at"),
        )


class SupplyChainBoundaryLog(BaseModel):
    """Boundary file/mail/system access ledger."""

    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    log_category = fields.CharField(max_length=30, description="file_operation/mail_outbound/system_access")
    operator_account = fields.CharField(max_length=255, null=True, description="操作账号")
    client_address = fields.CharField(max_length=100, null=True, description="客户端地址")
    personnel_ci_id = fields.CharField(max_length=36, null=True, description="人员 CI")
    endpoint_ci_id = fields.CharField(max_length=36, null=True, description="终端 CI")
    occurred_at = fields.DatetimeField(null=True, description="发生时间")
    source_type = fields.CharField(max_length=30, default="manual", description="来源")
    risk_flag = fields.BooleanField(default=False, description="风险标记")
    detail = fields.JSONField(default=dict, description="日志详情")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "accessory_project_id": self.accessory_project_id,
            "log_category": self.log_category,
            "operator_account": self.operator_account,
            "client_address": self.client_address,
            "personnel_ci_id": self.personnel_ci_id,
            "endpoint_ci_id": self.endpoint_ci_id,
            "occurred_at": strip_timezone_to_seconds(self.occurred_at),
            "source_type": self.source_type,
            "risk_flag": self.risk_flag,
            "detail": self.detail or {},
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_boundary_log"
        ordering = ["-occurred_at", "-utime"]
        indexes = (
            ("tenant_id", "accessory_project_id"),
            ("tenant_id", "occurred_at"),
            ("tenant_id", "accessory_project_id", "occurred_at"),
            ("tenant_id", "risk_flag"),
            ("tenant_id", "log_category"),
        )


class SupplyChainExceptionDeviceLog(BaseModel):
    """Exception device usage ledger."""

    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    exception_device_ci_id = fields.CharField(max_length=36, description="例外设备 CI")
    endpoint_ci_id = fields.CharField(max_length=36, null=True, description="终端 CI")
    personnel_ci_id = fields.CharField(max_length=36, null=True, description="人员 CI")
    log_type = fields.CharField(max_length=20, default="use", description="insert/remove/use")
    detail = fields.JSONField(default=dict, description="使用详情")
    occurred_at = fields.DatetimeField(null=True, description="发生时间")
    source_type = fields.CharField(max_length=30, default="manual", description="file_parse/manual")
    abnormal_type = fields.CharField(max_length=40, default="none", description="异常类型")
    status = fields.CharField(max_length=20, default="pending", description="pending/confirmed/ignored/converted")
    review_issue_id = fields.CharField(max_length=36, null=True, description="审查问题")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "accessory_project_id": self.accessory_project_id,
            "exception_device_ci_id": self.exception_device_ci_id,
            "endpoint_ci_id": self.endpoint_ci_id,
            "personnel_ci_id": self.personnel_ci_id,
            "log_type": self.log_type,
            "detail": self.detail or {},
            "occurred_at": strip_timezone_to_seconds(self.occurred_at),
            "source_type": self.source_type,
            "abnormal_type": self.abnormal_type,
            "status": self.status,
            "review_issue_id": self.review_issue_id,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_exception_device_log"
        ordering = ["-occurred_at", "-utime"]
        indexes = (
            ("tenant_id", "accessory_project_id"),
            ("tenant_id", "exception_device_ci_id"),
            ("tenant_id", "personnel_ci_id"),
            ("tenant_id", "occurred_at"),
            ("tenant_id", "accessory_project_id", "occurred_at"),
            ("tenant_id", "abnormal_type"),
            ("tenant_id", "status"),
        )


class SupplyChainCollectionTask(BaseModel):
    """Business wrapper around host collection abilities."""

    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    domain = fields.CharField(max_length=30, description="network/personnel_nda/boundary/exception_device")
    name = fields.CharField(max_length=255, description="任务名称")
    execution_mode = fields.CharField(max_length=30, default="manual", description="采集方式")
    playbook_id = fields.CharField(max_length=100, null=True, description="剧本 ID")
    playbook_version_id = fields.CharField(max_length=100, null=True, description="剧本版本 ID")
    action_id = fields.CharField(max_length=100, null=True, description="动作 ID")
    action_name = fields.CharField(max_length=255, null=True, description="动作名称")
    timed_task_id = fields.CharField(max_length=100, null=True, description="定时任务 ID")
    event_ingestion_id = fields.CharField(max_length=100, null=True, description="事件接入 ID")
    target_type = fields.CharField(max_length=50, null=True, description="目标类型")
    target_ci_ids = fields.JSONField(default=list, description="目标 CI")
    credential_ref = fields.CharField(max_length=255, null=True, description="凭据引用")
    input_params = fields.JSONField(default=dict, description="输入参数")
    parser_template_id = fields.CharField(max_length=36, null=True, description="解析模板")
    mapping_template_id = fields.CharField(max_length=36, null=True, description="映射模板")
    schedule_enabled = fields.BooleanField(default=False, description="是否启用调度")
    cron_expression = fields.CharField(max_length=100, null=True, description="Cron")
    after_success_action = fields.CharField(max_length=30, default="none", description="none/policy_compare/risk_evaluate")
    enabled = fields.BooleanField(default=True, description="是否启用")
    last_run_at = fields.DatetimeField(null=True, description="最近运行")
    last_run_status = fields.CharField(max_length=20, null=True, description="最近运行状态")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "accessory_project_id": self.accessory_project_id,
            "domain": self.domain,
            "name": self.name,
            "execution_mode": self.execution_mode,
            "playbook_id": self.playbook_id,
            "playbook_version_id": self.playbook_version_id,
            "action_id": self.action_id,
            "action_name": self.action_name,
            "timed_task_id": self.timed_task_id,
            "event_ingestion_id": self.event_ingestion_id,
            "target_type": self.target_type,
            "target_ci_ids": self.target_ci_ids or [],
            "credential_ref": self.credential_ref,
            "input_params": self.input_params or {},
            "parser_template_id": self.parser_template_id,
            "mapping_template_id": self.mapping_template_id,
            "schedule_enabled": self.schedule_enabled,
            "cron_expression": self.cron_expression,
            "after_success_action": self.after_success_action,
            "enabled": self.enabled,
            "last_run_at": strip_timezone_to_seconds(self.last_run_at),
            "last_run_status": self.last_run_status,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_collection_task"
        ordering = ["-utime"]
        indexes = (("tenant_id", "domain"), ("tenant_id", "accessory_project_id"), ("tenant_id", "enabled"))


class SupplyChainCollectionRun(BaseModel):
    """Collection run with run_id plumbing for external execution."""

    task_id = fields.CharField(max_length=36, null=True, description="采集任务")
    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    domain = fields.CharField(max_length=30, description="采集域")
    run_id = fields.CharField(max_length=64, description="运行 ID")
    playbook_run_id = fields.CharField(max_length=100, null=True, description="剧本运行 ID")
    action_run_id = fields.CharField(max_length=100, null=True, description="动作运行 ID")
    timed_run_id = fields.CharField(max_length=100, null=True, description="定时运行 ID")
    event_batch_id = fields.CharField(max_length=100, null=True, description="事件批次")
    status = fields.CharField(max_length=20, default="pending", description="pending/running/success/failed/partial")
    started_at = fields.DatetimeField(null=True, description="开始时间")
    finished_at = fields.DatetimeField(null=True, description="结束时间")
    raw_payload_ref = fields.CharField(max_length=255, null=True, description="原始结果引用")
    parsed_result_ref = fields.CharField(max_length=255, null=True, description="解析结果引用")
    total_count = fields.IntField(default=0, description="总数")
    success_count = fields.IntField(default=0, description="成功数")
    failed_count = fields.IntField(default=0, description="失败数")
    error_message = fields.TextField(null=True, description="错误信息")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "task_id": self.task_id,
            "accessory_project_id": self.accessory_project_id,
            "domain": self.domain,
            "run_id": self.run_id,
            "playbook_run_id": self.playbook_run_id,
            "action_run_id": self.action_run_id,
            "timed_run_id": self.timed_run_id,
            "event_batch_id": self.event_batch_id,
            "status": self.status,
            "started_at": strip_timezone_to_seconds(self.started_at),
            "finished_at": strip_timezone_to_seconds(self.finished_at),
            "raw_payload_ref": self.raw_payload_ref,
            "parsed_result_ref": self.parsed_result_ref,
            "total_count": self.total_count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "error_message": self.error_message,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_collection_run"
        ordering = ["-ctime"]
        indexes = (
            ("tenant_id", "task_id"),
            ("tenant_id", "domain"),
            ("tenant_id", "accessory_project_id"),
            ("tenant_id", "run_id"),
            ("tenant_id", "status"),
        )


class SupplyChainParseMappingTemplate(BaseModel):
    """Parser and mapping rules for collection results."""

    domain = fields.CharField(max_length=30, description="采集域")
    name = fields.CharField(max_length=255, description="模板名称")
    source_format = fields.CharField(max_length=20, default="json", description="json/csv/text/raw_config/log")
    parser_type = fields.CharField(max_length=20, default="builtin", description="builtin/regex/json_path/script")
    parser_rules = fields.JSONField(default=dict, description="解析规则")
    mapping_rules = fields.JSONField(default=dict, description="映射规则")
    enabled = fields.BooleanField(default=True, description="是否启用")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "domain": self.domain,
            "name": self.name,
            "source_format": self.source_format,
            "parser_type": self.parser_type,
            "parser_rules": self.parser_rules or {},
            "mapping_rules": self.mapping_rules or {},
            "enabled": self.enabled,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_parse_mapping_template"
        ordering = ["-utime"]
        indexes = (("tenant_id", "domain"), ("tenant_id", "enabled"))


class SupplyChainPresetPolicy(BaseModel):
    """Expected network policy baseline."""

    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    device_ci_id = fields.CharField(max_length=36, null=True, description="网络设备 CI")
    rule_name = fields.CharField(max_length=255, description="规则名称")
    source_zone = fields.CharField(max_length=100, null=True, description="源区域")
    source_address = fields.CharField(max_length=255, null=True, description="源地址")
    target_zone = fields.CharField(max_length=100, null=True, description="目标区域")
    target_address = fields.CharField(max_length=255, null=True, description="目标地址")
    protocol = fields.CharField(max_length=30, null=True, description="协议")
    port = fields.CharField(max_length=100, null=True, description="端口")
    action = fields.CharField(max_length=20, default="permit", description="permit/deny")
    business_purpose = fields.TextField(null=True, description="业务用途")
    approval_status = fields.CharField(max_length=30, null=True, description="外部审批状态")
    effective_status = fields.CharField(max_length=20, default="active", description="active/disabled")
    valid_from = fields.DateField(null=True, description="生效日期")
    valid_to = fields.DateField(null=True, description="失效日期")
    risk_level = fields.CharField(max_length=20, default="medium", description="风险等级")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "accessory_project_id": self.accessory_project_id,
            "device_ci_id": self.device_ci_id,
            "rule_name": self.rule_name,
            "source_zone": self.source_zone,
            "source_address": self.source_address,
            "target_zone": self.target_zone,
            "target_address": self.target_address,
            "protocol": self.protocol,
            "port": self.port,
            "action": self.action,
            "business_purpose": self.business_purpose,
            "approval_status": self.approval_status,
            "effective_status": self.effective_status,
            "valid_from": str(self.valid_from) if self.valid_from else None,
            "valid_to": str(self.valid_to) if self.valid_to else None,
            "risk_level": self.risk_level,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_preset_policy"
        ordering = ["-utime"]
        indexes = (("tenant_id", "accessory_project_id"), ("tenant_id", "device_ci_id"), ("tenant_id", "effective_status"))


class SupplyChainActualPolicy(BaseModel):
    """Actual policy parsed from network collection."""

    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    device_ci_id = fields.CharField(max_length=36, null=True, description="网络设备 CI")
    collection_run_id = fields.CharField(max_length=36, null=True, description="采集运行")
    raw_rule_id = fields.CharField(max_length=255, null=True, description="原始规则 ID")
    rule_name = fields.CharField(max_length=255, null=True, description="规则名称")
    source_zone = fields.CharField(max_length=100, null=True, description="源区域")
    source_address = fields.CharField(max_length=255, null=True, description="源地址")
    target_zone = fields.CharField(max_length=100, null=True, description="目标区域")
    target_address = fields.CharField(max_length=255, null=True, description="目标地址")
    protocol = fields.CharField(max_length=30, null=True, description="协议")
    port = fields.CharField(max_length=100, null=True, description="端口")
    action = fields.CharField(max_length=20, default="permit", description="permit/deny")
    raw_text = fields.TextField(null=True, description="原始文本")
    parse_status = fields.CharField(max_length=20, default="parsed", description="parsed/unparsed")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "accessory_project_id": self.accessory_project_id,
            "device_ci_id": self.device_ci_id,
            "collection_run_id": self.collection_run_id,
            "raw_rule_id": self.raw_rule_id,
            "rule_name": self.rule_name,
            "source_zone": self.source_zone,
            "source_address": self.source_address,
            "target_zone": self.target_zone,
            "target_address": self.target_address,
            "protocol": self.protocol,
            "port": self.port,
            "action": self.action,
            "raw_text": self.raw_text,
            "parse_status": self.parse_status,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_actual_policy"
        ordering = ["-utime"]
        indexes = (("tenant_id", "accessory_project_id"), ("tenant_id", "device_ci_id"), ("tenant_id", "collection_run_id"), ("tenant_id", "parse_status"))


class SupplyChainPolicyCompareRun(BaseModel):
    """Policy compare run summary."""

    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    collection_run_id = fields.CharField(max_length=36, null=True, description="采集运行")
    total = fields.IntField(default=0, description="总数")
    matched = fields.IntField(default=0, description="匹配数")
    added = fields.IntField(default=0, description="新增数")
    missing = fields.IntField(default=0, description="缺失数")
    changed = fields.IntField(default=0, description="变更数")
    unparsed = fields.IntField(default=0, description="未解析数")
    started_at = fields.DatetimeField(null=True, description="开始时间")
    finished_at = fields.DatetimeField(null=True, description="结束时间")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "accessory_project_id": self.accessory_project_id,
            "collection_run_id": self.collection_run_id,
            "total": self.total,
            "matched": self.matched,
            "added": self.added,
            "missing": self.missing,
            "changed": self.changed,
            "unparsed": self.unparsed,
            "started_at": strip_timezone_to_seconds(self.started_at),
            "finished_at": strip_timezone_to_seconds(self.finished_at),
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_policy_compare_run"
        ordering = ["-ctime"]
        indexes = (("tenant_id", "accessory_project_id"), ("tenant_id", "collection_run_id"))


class SupplyChainPolicyCompareItem(BaseModel):
    """Single policy compare item."""

    compare_run_id = fields.CharField(max_length=36, description="比对运行")
    preset_policy_id = fields.CharField(max_length=36, null=True, description="预设策略")
    actual_policy_id = fields.CharField(max_length=36, null=True, description="实际策略")
    compare_type = fields.CharField(max_length=20, description="matched/added/missing/changed/unparsed")
    risk_level = fields.CharField(max_length=20, default="medium", description="风险等级")
    handle_status = fields.CharField(max_length=20, default="pending", description="pending/confirmed/ignored/converted")
    review_issue_id = fields.CharField(max_length=36, null=True, description="审查问题")
    diff = fields.JSONField(default=dict, description="差异")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "compare_run_id": self.compare_run_id,
            "preset_policy_id": self.preset_policy_id,
            "actual_policy_id": self.actual_policy_id,
            "compare_type": self.compare_type,
            "risk_level": self.risk_level,
            "handle_status": self.handle_status,
            "review_issue_id": self.review_issue_id,
            "diff": self.diff or {},
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_policy_compare_item"
        ordering = ["-utime"]
        indexes = (("tenant_id", "compare_run_id"), ("tenant_id", "compare_type"), ("tenant_id", "handle_status"))


class SupplyChainPolicyChange(BaseModel):
    """Detected policy change with external callback placeholders."""

    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    device_ci_id = fields.CharField(max_length=36, null=True, description="网络设备 CI")
    policy_name = fields.CharField(max_length=255, null=True, description="策略名称")
    change_type = fields.CharField(max_length=20, description="added/modified")
    diff_text = fields.TextField(null=True, description="差异文本")
    detected_at = fields.DatetimeField(null=True, description="发现时间")
    risk_level = fields.CharField(max_length=20, default="high", description="风险等级")
    external_approve_ref = fields.CharField(max_length=100, null=True, description="外部审批单号")
    external_approve_result = fields.CharField(max_length=100, null=True, description="外部审批结果")
    review_issue_id = fields.CharField(max_length=36, null=True, description="审查问题")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "accessory_project_id": self.accessory_project_id,
            "device_ci_id": self.device_ci_id,
            "policy_name": self.policy_name,
            "change_type": self.change_type,
            "diff_text": self.diff_text,
            "detected_at": strip_timezone_to_seconds(self.detected_at),
            "risk_level": self.risk_level,
            "external_approve_ref": self.external_approve_ref,
            "external_approve_result": self.external_approve_result,
            "review_issue_id": self.review_issue_id,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_policy_change"
        ordering = ["-detected_at", "-utime"]
        indexes = (("tenant_id", "accessory_project_id"), ("tenant_id", "device_ci_id"), ("tenant_id", "change_type"), ("tenant_id", "detected_at"))


class SupplyChainNetworkTopology(BaseModel):
    """Stored LogicFlow network topology."""

    project_area_id = fields.CharField(max_length=36, null=True, description="厂区/区域 CI")
    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    name = fields.CharField(max_length=255, description="拓扑名称")
    version = fields.CharField(max_length=50, default="v1", description="版本")
    graph_data = fields.JSONField(default=dict, description="LogicFlow 数据")
    change_summary = fields.TextField(null=True, description="变更说明")
    status = fields.CharField(max_length=20, default="active", description="active/archived")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "project_area_id": self.project_area_id,
            "accessory_project_id": self.accessory_project_id,
            "name": self.name,
            "version": self.version,
            "graph_data": self.graph_data or {},
            "change_summary": self.change_summary,
            "status": self.status,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_network_topology"
        ordering = ["-utime"]
        indexes = (("tenant_id", "project_area_id"), ("tenant_id", "accessory_project_id"), ("tenant_id", "status"))


class SupplyChainReviewIssue(BaseModel):
    """Review issue and remediation closure workflow."""

    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    domain = fields.CharField(max_length=30, description="问题域")
    source_object_type = fields.CharField(max_length=80, null=True, description="来源对象类型")
    source_object_id = fields.CharField(max_length=36, null=True, description="来源对象 ID")
    issue_no = fields.CharField(max_length=64, description="问题编号")
    title = fields.CharField(max_length=255, description="标题")
    description = fields.TextField(null=True, description="描述")
    risk_level = fields.CharField(max_length=20, default="medium", description="风险等级")
    remediation_requirement = fields.TextField(null=True, description="整改要求")
    responsible_person = fields.CharField(max_length=255, null=True, description="责任人")
    due_date = fields.DateField(null=True, description="截止日期")
    status = fields.CharField(max_length=30, default="pending", description="pending/in_progress/pending_review/closed/returned")
    attachment_ids = fields.JSONField(default=list, description="附件")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "accessory_project_id": self.accessory_project_id,
            "domain": self.domain,
            "source_object_type": self.source_object_type,
            "source_object_id": self.source_object_id,
            "issue_no": self.issue_no,
            "title": self.title,
            "description": self.description,
            "risk_level": self.risk_level,
            "remediation_requirement": self.remediation_requirement,
            "responsible_person": self.responsible_person,
            "due_date": str(self.due_date) if self.due_date else None,
            "status": self.status,
            "attachment_ids": self.attachment_ids or [],
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_review_issue"
        ordering = ["-utime"]
        unique_together = (("tenant_id", "issue_no"),)
        indexes = (("tenant_id", "accessory_project_id"), ("tenant_id", "domain"), ("tenant_id", "status"), ("tenant_id", "risk_level"))


class SupplyChainRemediationRecord(BaseModel):
    """Status transition and remediation evidence record."""

    review_issue_id = fields.CharField(max_length=36, description="审查问题")
    action_type = fields.CharField(max_length=30, description="动作类型")
    content = fields.TextField(null=True, description="内容")
    attachment_ids = fields.JSONField(default=list, description="附件")
    operator = fields.CharField(max_length=255, null=True, description="操作人")
    operated_at = fields.DatetimeField(null=True, description="操作时间")
    from_status = fields.CharField(max_length=30, null=True, description="原状态")
    to_status = fields.CharField(max_length=30, null=True, description="新状态")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "review_issue_id": self.review_issue_id,
            "action_type": self.action_type,
            "content": self.content,
            "attachment_ids": self.attachment_ids or [],
            "operator": self.operator,
            "operated_at": strip_timezone_to_seconds(self.operated_at),
            "from_status": self.from_status,
            "to_status": self.to_status,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_remediation_record"
        ordering = ["-operated_at", "-ctime"]
        indexes = (("tenant_id", "review_issue_id"), ("tenant_id", "action_type"))


class SupplyChainDisposalRule(BaseModel):
    """Manual-confirm disposal rule."""

    project_area_id = fields.CharField(max_length=36, null=True, description="厂区 CI")
    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    trigger_type = fields.CharField(max_length=60, description="触发类型")
    enabled = fields.BooleanField(default=True, description="是否启用")
    execution_mode = fields.CharField(max_length=30, default="manual_confirm", description="执行模式")
    actions = fields.JSONField(default=list, description="处置动作")
    playbook_id = fields.CharField(max_length=100, null=True, description="剧本 ID")
    action_id = fields.CharField(max_length=100, null=True, description="动作 ID")
    input_mapping = fields.JSONField(default=dict, description="输入映射")
    require_review_issue = fields.BooleanField(default=False, description="是否需要问题")
    risk_level = fields.CharField(max_length=20, default="medium", description="风险等级")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "project_area_id": self.project_area_id,
            "accessory_project_id": self.accessory_project_id,
            "trigger_type": self.trigger_type,
            "enabled": self.enabled,
            "execution_mode": self.execution_mode,
            "actions": self.actions or [],
            "playbook_id": self.playbook_id,
            "action_id": self.action_id,
            "input_mapping": self.input_mapping or {},
            "require_review_issue": self.require_review_issue,
            "risk_level": self.risk_level,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_disposal_rule"
        ordering = ["-utime"]
        indexes = (("tenant_id", "project_area_id"), ("tenant_id", "accessory_project_id"), ("tenant_id", "trigger_type"), ("tenant_id", "enabled"))


class SupplyChainDisposalTask(BaseModel):
    """Manual-confirm disposal task with run_id tracking."""

    rule_id = fields.CharField(max_length=36, null=True, description="规则")
    trigger_type = fields.CharField(max_length=60, description="触发类型")
    project_area_id = fields.CharField(max_length=36, null=True, description="厂区 CI")
    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    personnel_ci_id = fields.CharField(max_length=36, null=True, description="人员 CI")
    related_ci_ids = fields.JSONField(default=list, description="关联 CI")
    actions = fields.JSONField(default=list, description="动作")
    status = fields.CharField(max_length=30, default="pending_confirm", description="pending_confirm/executing/success/failed/skipped/cancelled")
    confirm_by = fields.CharField(max_length=255, null=True, description="确认人")
    confirmed_at = fields.DatetimeField(null=True, description="确认时间")
    run_id = fields.CharField(max_length=64, null=True, description="运行 ID")
    result_message = fields.TextField(null=True, description="结果")
    review_issue_id = fields.CharField(max_length=36, null=True, description="审查问题")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "rule_id": self.rule_id,
            "trigger_type": self.trigger_type,
            "project_area_id": self.project_area_id,
            "accessory_project_id": self.accessory_project_id,
            "personnel_ci_id": self.personnel_ci_id,
            "related_ci_ids": self.related_ci_ids or [],
            "actions": self.actions or [],
            "status": self.status,
            "confirm_by": self.confirm_by,
            "confirmed_at": strip_timezone_to_seconds(self.confirmed_at),
            "run_id": self.run_id,
            "result_message": self.result_message,
            "review_issue_id": self.review_issue_id,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_disposal_task"
        ordering = ["-utime"]
        indexes = (("tenant_id", "project_area_id"), ("tenant_id", "accessory_project_id"), ("tenant_id", "personnel_ci_id"), ("tenant_id", "status"), ("tenant_id", "run_id"))


class SupplyChainAttachmentRef(BaseModel):
    """S3/RustFS object reference."""

    object_type = fields.CharField(max_length=80, description="对象类型")
    object_id = fields.CharField(max_length=36, description="对象 ID")
    file_id = fields.CharField(max_length=255, description="S3 路径")
    file_name = fields.CharField(max_length=255, description="文件名")
    file_type = fields.CharField(max_length=100, null=True, description="文件类型")
    file_size = fields.IntField(default=0, description="文件大小")
    uploaded_by = fields.CharField(max_length=255, null=True, description="上传人")
    uploaded_at = fields.DatetimeField(null=True, description="上传时间")
    remark = fields.TextField(null=True, description="备注")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "object_type": self.object_type,
            "object_id": self.object_id,
            "file_id": self.file_id,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "uploaded_by": self.uploaded_by,
            "uploaded_at": strip_timezone_to_seconds(self.uploaded_at),
            "remark": self.remark,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_attachment_ref"
        ordering = ["-uploaded_at", "-ctime"]
        indexes = (("tenant_id", "object_type", "object_id"), ("tenant_id", "file_id"))


class SupplyChainNotificationEvent(BaseModel):
    """notify_config-compatible notification event payload."""

    event_type = fields.CharField(max_length=80, description="事件类型")
    title = fields.CharField(max_length=255, description="标题")
    content = fields.TextField(null=True, description="内容")
    risk_level = fields.CharField(max_length=20, null=True, description="风险等级")
    target_type = fields.CharField(max_length=80, null=True, description="目标类型")
    target_id = fields.CharField(max_length=36, null=True, description="目标 ID")
    project_area_id = fields.CharField(max_length=36, null=True, description="厂区 CI")
    accessory_project_id = fields.CharField(max_length=36, null=True, description="配件项目 CI")
    link = fields.CharField(max_length=255, null=True, description="链接")
    receivers = fields.JSONField(default=list, description="接收人")
    receiver_roles = fields.JSONField(default=list, description="接收角色")
    status = fields.CharField(max_length=20, default="pending", description="pending/sent/failed")
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return base_json(self) | {
            "event_type": self.event_type,
            "title": self.title,
            "content": self.content,
            "risk_level": self.risk_level,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "project_area_id": self.project_area_id,
            "accessory_project_id": self.accessory_project_id,
            "link": self.link,
            "receivers": self.receivers or [],
            "receiver_roles": self.receiver_roles or [],
            "status": self.status,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "supply_chain_notification_event"
        ordering = ["-ctime"]
        indexes = (("tenant_id", "event_type"), ("tenant_id", "target_type", "target_id"), ("tenant_id", "status"))


def base_json(item: BaseModel) -> dict:
    return {
        "id": item.id,
        "ctime": strip_timezone_to_seconds(item.ctime),
        "utime": strip_timezone_to_seconds(item.utime),
        "tenant_id": item.tenant_id,
    }
