# coding=utf-8
from __future__ import annotations

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel
from tortoise import fields


ASSET_MODEL_STATUS_ACTIVE = "active"
ASSET_MODEL_STATUS_DISABLED = "disabled"
ASSET_STATUS_ACTIVE = "active"
ASSET_STATUS_DISABLED = "disabled"
ASSET_STATUS_ARCHIVED = "archived"
ASSET_RISK_STATUS_NORMAL = "normal"

FIELD_DATA_TYPES = {
    "text",
    "number",
    "date",
    "datetime",
    "enum",
    "bool",
    "attachment",
    "ref",
    "organization",
    "user",
    "tag",
}


def _policy_data_value(data: dict, *keys: str, fallback=None):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return fallback


class AssetModelGroup(BaseModel):
    """Tenant-visible grouping for builtin and custom asset models."""

    key = fields.CharField(max_length=64, description="分组唯一标识")
    name = fields.CharField(max_length=255, description="分组名称")
    sort = fields.IntField(default=0, description="展示顺序")
    is_builtin = fields.BooleanField(default=False, description="产品内置分组")
    is_locked = fields.BooleanField(default=False, description="内置结构锁定")
    builtin_version = fields.CharField(max_length=64, null=True, description="内置版本")
    builtin_hash = fields.CharField(max_length=64, null=True, description="定义摘要")
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
            "sort": self.sort,
            "is_builtin": self.is_builtin,
            "builtin_version": self.builtin_version,
            "builtin_hash": self.builtin_hash,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "asset_model_group"
        ordering = ["sort", "key"]
        unique_together = (("tenant_id", "key"),)
        indexes = (("tenant_id", "sort"), ("tenant_id", "is_builtin"))


class AssetModel(BaseModel):
    """Asset model definition for managed assets."""

    key = fields.CharField(max_length=64, description="模型唯一标识")
    name = fields.CharField(max_length=255, description="模型名称")
    icon = fields.CharField(max_length=100, null=True, description="图标")
    group_id = fields.CharField(max_length=36, description="所属分组 ID")
    group_key = fields.CharField(max_length=64, description="所属分组 key")
    primary_field = fields.CharField(
        max_length=64, default="", description="主展示字段"
    )
    status = fields.CharField(
        max_length=20, default=ASSET_MODEL_STATUS_ACTIVE, description="active/disabled"
    )
    is_locked = fields.BooleanField(default=False, description="交付锁定标记")
    is_builtin = fields.BooleanField(default=False, description="产品内置模型")
    builtin_key = fields.CharField(max_length=64, null=True, description="内置稳定标识")
    builtin_version = fields.CharField(max_length=64, null=True, description="内置版本")
    builtin_hash = fields.CharField(max_length=64, null=True, description="定义摘要")
    owner = fields.CharField(
        max_length=20, default="tenant", description="product/tenant"
    )
    sort = fields.IntField(default=0, description="展示顺序")
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
            "group_id": self.group_id,
            "group_key": self.group_key,
            "primary_field": self.primary_field,
            "status": self.status,
            "is_builtin": self.is_builtin,
            "builtin_key": self.builtin_key,
            "builtin_version": self.builtin_version,
            "builtin_hash": self.builtin_hash,
            "owner": self.owner,
            "sort": self.sort,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "asset_model"
        ordering = ["sort", "key"]
        unique_together = (("tenant_id", "key"),)
        indexes = (
            ("tenant_id", "status"),
            ("tenant_id", "group_id", "sort"),
            ("tenant_id", "is_builtin"),
        )


class AssetFieldDefinition(BaseModel):
    """Dynamic field definition for an asset model."""

    asset_model_id = fields.CharField(max_length=36, description="所属模型 ID")
    asset_model_key = fields.CharField(max_length=64, description="所属模型 key")
    field_key = fields.CharField(max_length=64, description="字段 key")
    source_field_key = fields.CharField(
        max_length=64, null=True, description="来源系统字段 key"
    )
    label = fields.CharField(max_length=255, description="字段名称")
    data_type = fields.CharField(max_length=20, description="字段类型")
    enum_options = fields.JSONField(default=list, description="枚举选项")
    options = fields.JSONField(default=dict, description="类型选项")
    referenced_asset_model_id = fields.CharField(
        max_length=36, null=True, description="引用模型 ID"
    )
    referenced_asset_model_key = fields.CharField(
        max_length=64, null=True, description="引用模型 key"
    )
    required = fields.BooleanField(default=False, description="是否必填")
    unique = fields.BooleanField(default=False, description="是否唯一")
    editable = fields.BooleanField(default=True, description="是否可编辑")
    is_derived = fields.BooleanField(default=False, description="派生展示字段")
    field_group = fields.CharField(
        max_length=255, default="其他", description="字段分组"
    )
    field_group_sort = fields.IntField(default=0, description="字段分组顺序")
    user_prompt = fields.TextField(null=True, description="字段提示")
    default_value = fields.JSONField(null=True, description="默认值")
    sort = fields.IntField(default=0, description="排序")
    is_locked = fields.BooleanField(default=False, description="锁定字段")
    is_builtin = fields.BooleanField(default=False, description="产品内置字段")
    builtin_key = fields.CharField(
        max_length=160, null=True, description="内置稳定标识"
    )
    builtin_version = fields.CharField(max_length=64, null=True, description="内置版本")
    builtin_hash = fields.CharField(max_length=64, null=True, description="定义摘要")
    owner = fields.CharField(
        max_length=20, default="tenant", description="product/tenant"
    )
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "asset_model_id": self.asset_model_id,
            "asset_model_key": self.asset_model_key,
            "field_key": self.field_key,
            "source_field_key": self.source_field_key,
            "label": self.label,
            "data_type": self.data_type,
            "enum_options": self.enum_options or [],
            "options": self.options or {},
            "referenced_asset_model_id": self.referenced_asset_model_id,
            "referenced_asset_model_key": self.referenced_asset_model_key,
            "required": self.required,
            "unique": self.unique,
            "editable": self.editable,
            "is_derived": self.is_derived,
            "user_prompt": self.user_prompt,
            "default_value": self.default_value,
            "sort": self.sort,
            "is_builtin": self.is_builtin,
            "builtin_key": self.builtin_key,
            "builtin_version": self.builtin_version,
            "builtin_hash": self.builtin_hash,
            "owner": self.owner,
            "creator": self.creator,
            "updater": self.updater,
        }

    class Meta:
        table = "asset_field_definition"
        ordering = ["sort", "ctime"]
        unique_together = (("tenant_id", "asset_model_id", "field_key"),)
        indexes = (
            ("tenant_id", "asset_model_id"),
            ("tenant_id", "data_type"),
            ("tenant_id", "asset_model_id", "field_group_sort"),
        )


class AssetUniqueRule(BaseModel):
    """Model-level compound uniqueness rule."""

    asset_model_id = fields.CharField(max_length=36, description="所属模型 ID")
    asset_model_key = fields.CharField(max_length=64, description="所属模型 key")
    key = fields.CharField(max_length=64, description="规则稳定标识")
    field_keys = fields.JSONField(default=list, description="联合唯一字段")
    sort = fields.IntField(default=0, description="规则顺序")
    is_locked = fields.BooleanField(default=False, description="锁定规则")
    is_builtin = fields.BooleanField(default=False, description="产品内置规则")
    builtin_key = fields.CharField(
        max_length=160, null=True, description="内置稳定标识"
    )
    builtin_version = fields.CharField(max_length=64, null=True, description="内置版本")
    builtin_hash = fields.CharField(max_length=64, null=True, description="定义摘要")
    owner = fields.CharField(
        max_length=20, default="tenant", description="product/tenant"
    )
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "asset_model_id": self.asset_model_id,
            "asset_model_key": self.asset_model_key,
            "key": self.key,
            "field_keys": self.field_keys or [],
            "sort": self.sort,
            "is_builtin": self.is_builtin,
            "builtin_key": self.builtin_key,
            "builtin_version": self.builtin_version,
            "builtin_hash": self.builtin_hash,
            "owner": self.owner,
        }

    class Meta:
        table = "asset_unique_rule"
        ordering = ["sort", "key"]
        unique_together = (("tenant_id", "asset_model_id", "key"),)
        indexes = (("tenant_id", "asset_model_id"),)


class AssetBuiltinCatalogState(BaseModel):
    """Installed builtin-catalog revision for one tenant."""

    catalog_key = fields.CharField(max_length=64, description="目录标识")
    catalog_version = fields.CharField(max_length=64, description="目录版本")
    catalog_digest = fields.CharField(max_length=80, description="目录摘要")
    revision = fields.IntField(default=0, description="租户目录状态版本")
    status = fields.CharField(max_length=32, default="ready", description="目录状态")
    onboarding_completed = fields.BooleanField(
        default=False,
        description="首次启用引导是否已完成",
    )
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return {
            "catalog_key": self.catalog_key,
            "catalog_version": self.catalog_version,
            "catalog_digest": self.catalog_digest,
            "revision": self.revision,
            "status": self.status,
            "onboarding_completed": self.onboarding_completed,
            "utime": strip_timezone_to_seconds(self.utime),
        }

    class Meta:
        table = "asset_builtin_catalog_state"
        unique_together = (("tenant_id", "catalog_key"),)
        indexes = (("tenant_id", "status"),)


class AssetBuiltinCatalogEvent(BaseModel):
    """Audit event for builtin-catalog initialization and reconciliation."""

    request_id = fields.CharField(max_length=64, description="协调请求 ID")
    action = fields.CharField(
        max_length=20, default="reconcile", description="协调动作"
    )
    status = fields.CharField(
        max_length=20,
        description="succeeded/unchanged/failed",
    )
    catalog_version = fields.CharField(max_length=64, description="目录版本")
    catalog_digest = fields.CharField(max_length=80, description="目录摘要")
    before_state = fields.JSONField(default=dict, description="协调前状态")
    after_state = fields.JSONField(default=dict, description="协调后状态")
    changes = fields.JSONField(default=dict, description="变更数量")
    failure_reason = fields.CharField(
        max_length=255,
        null=True,
        description="失败类型",
    )
    operator = fields.CharField(max_length=255, null=True, description="操作人")

    @property
    def json(self) -> dict:
        return {
            "request_id": self.request_id,
            "action": self.action,
            "status": self.status,
            "catalog_version": self.catalog_version,
            "catalog_digest": self.catalog_digest,
            "before_state": self.before_state or {},
            "after_state": self.after_state or {},
            "changes": self.changes or {},
            "failure_reason": self.failure_reason,
            "operator": self.operator,
            "ctime": strip_timezone_to_seconds(self.ctime),
        }

    class Meta:
        table = "asset_builtin_catalog_event"
        unique_together = (("tenant_id", "request_id"),)
        indexes = (("tenant_id", "ctime"), ("tenant_id", "status"))


class AssetBuiltinActivation(BaseModel):
    """Tenant-owned activation state separated from builtin definitions."""

    subject_type = fields.CharField(max_length=20, default="model", description="model")
    subject_key = fields.CharField(max_length=64, description="模型稳定标识")
    enabled = fields.BooleanField(default=False, description="是否启用")
    revision = fields.IntField(default=0, description="状态版本")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    class Meta:
        table = "asset_builtin_activation"
        unique_together = (("tenant_id", "subject_type", "subject_key"),)
        indexes = (("tenant_id", "enabled"),)


class AssetBuiltinActivationPreview(BaseModel):
    """Short-lived impact preview required before activation changes."""

    token = fields.CharField(max_length=64, description="一次性预览令牌")
    action = fields.CharField(max_length=16, description="enable/disable")
    scope = fields.CharField(max_length=16, description="model/group/selection/all")
    subject_keys = fields.JSONField(default=list, description="操作对象")
    catalog_version = fields.CharField(max_length=64, description="目录版本")
    tenant_revision = fields.IntField(default=0, description="租户状态版本")
    impact = fields.JSONField(default=dict, description="影响摘要")
    expires_at = fields.DatetimeField(description="过期时间")
    used = fields.BooleanField(default=False, description="是否已使用")
    creator = fields.CharField(max_length=255, null=True, description="创建人")

    class Meta:
        table = "asset_builtin_activation_preview"
        unique_together = (("tenant_id", "token"),)
        indexes = (("tenant_id", "expires_at"),)


class AssetBuiltinActivationEvent(BaseModel):
    """Idempotent audit event for a tenant activation transaction."""

    request_id = fields.CharField(max_length=64, description="幂等请求 ID")
    action = fields.CharField(max_length=16, description="enable/disable")
    scope = fields.CharField(max_length=16, description="model/group/selection/all")
    subject_keys = fields.JSONField(default=list, description="操作对象")
    before_state = fields.JSONField(default=dict, description="变更前状态")
    after_state = fields.JSONField(default=dict, description="变更后状态")
    impact = fields.JSONField(default=dict, description="影响摘要")
    catalog_version = fields.CharField(max_length=64, description="目录版本")
    tenant_revision = fields.IntField(default=0, description="变更后版本")
    operator = fields.CharField(max_length=255, null=True, description="操作人")

    @property
    def json(self) -> dict:
        return {
            "request_id": self.request_id,
            "action": self.action,
            "scope": self.scope,
            "subject_keys": self.subject_keys or [],
            "before_state": self.before_state or {},
            "after_state": self.after_state or {},
            "impact": self.impact or {},
            "catalog_version": self.catalog_version,
            "tenant_revision": self.tenant_revision,
            "operator": self.operator,
            "ctime": strip_timezone_to_seconds(self.ctime),
        }

    class Meta:
        table = "asset_builtin_activation_event"
        unique_together = (("tenant_id", "request_id"),)
        indexes = (("tenant_id", "ctime"),)


class AssetInstance(BaseModel):
    """Asset instance with dynamic attributes."""

    asset_model_id = fields.CharField(max_length=36, description="模型 ID")
    asset_model_key = fields.CharField(max_length=64, description="模型 key")
    name = fields.CharField(max_length=255, description="实例名称")
    project_area_id = fields.CharField(
        max_length=36, null=True, description="厂区/区域资产 ID"
    )
    accessory_project_id = fields.CharField(
        max_length=36, null=True, description="配件项目资产 ID"
    )
    attributes = fields.JSONField(default=dict, description="动态属性")
    status = fields.CharField(
        max_length=20,
        default=ASSET_STATUS_ACTIVE,
        description="active/disabled/archived",
    )
    risk_status = fields.CharField(
        max_length=20,
        default=ASSET_RISK_STATUS_NORMAL,
        description="normal/low/medium/high",
    )
    creator = fields.CharField(max_length=255, null=True, description="创建人")
    updater = fields.CharField(max_length=255, null=True, description="更新人")

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "asset_model_id": self.asset_model_id,
            "asset_model_key": self.asset_model_key,
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
        table = "asset_instance"
        ordering = ["-utime"]
        indexes = (
            ("tenant_id", "asset_model_id"),
            ("tenant_id", "asset_model_id", "status"),
            ("tenant_id", "project_area_id"),
            ("tenant_id", "accessory_project_id"),
            ("tenant_id", "asset_model_id", "project_area_id", "accessory_project_id"),
        )


class AssetReference(BaseModel):
    """Derived index for model-reference fields on asset instances."""

    field_key = fields.CharField(max_length=64, description="引用字段 key")
    source_asset = fields.ForeignKeyField(
        "models.AssetInstance",
        related_name="outgoing_references",
        description="持有引用的资产",
        on_delete=fields.CASCADE,
    )
    target_asset = fields.ForeignKeyField(
        "models.AssetInstance",
        related_name="incoming_references",
        description="被引用的资产",
        on_delete=fields.CASCADE,
    )
    position = fields.IntField(default=0, description="多值引用顺序")

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "field_key": self.field_key,
            "source_asset_id": self.source_asset_id,
            "target_asset_id": self.target_asset_id,
            "position": self.position,
        }

    class Meta:
        table = "asset_reference"
        ordering = ["source_asset_id", "field_key", "position"]
        indexes = (
            ("tenant_id", "source_asset_id"),
            ("tenant_id", "target_asset_id"),
            ("tenant_id", "source_asset_id", "field_key"),
        )
        unique_together = (
            ("tenant_id", "source_asset_id", "field_key", "target_asset_id"),
        )
