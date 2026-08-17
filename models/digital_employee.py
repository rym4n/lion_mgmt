# coding=utf-8
from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel, EncryptedCharField
from tortoise import fields


class DigitalEmployeeRole(BaseModel):
    """数字员工岗位索引。"""

    role_key = fields.CharField(max_length=64, description="岗位逻辑标识")
    name = fields.CharField(max_length=255, description="岗位名称")
    template_dir = fields.CharField(max_length=512, description="岗位模板目录")
    default_skill_keys = fields.JSONField(default=list, description="默认技能 key 列表")
    default_rbac_role_id = fields.CharField(max_length=36, null=True, description="默认绑定 RBAC 角色")
    license_required = fields.BooleanField(default=False, description="是否需要 license 授权")
    version = fields.IntField(default=1, description="岗位模板版本")
    is_builtin = fields.BooleanField(default=False, description="是否内置")
    enabled = fields.BooleanField(default=True, description="是否启用")
    meta = fields.JSONField(default=dict, description="扩展元数据")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "role_key": self.role_key,
            "name": self.name,
            "template_dir": self.template_dir,
            "default_skill_keys": self.default_skill_keys,
            "default_rbac_role_id": self.default_rbac_role_id,
            "license_required": self.license_required,
            "version": self.version,
            "is_builtin": self.is_builtin,
            "enabled": self.enabled,
            "meta": self.meta,
        }

    class Meta:
        table = "digital_employee_role"
        ordering = ["-utime"]
        unique_together = (("tenant_id", "role_key"),)
        indexes = (("tenant_id", "is_builtin"),)


class DigitalEmployeeSkill(BaseModel):
    """数字员工技能索引。权威内容在 skill_dir 下的 SKILL.md，本表仅供列表与调度查询。"""

    skill_key = fields.CharField(max_length=64, description="技能逻辑标识")
    name = fields.CharField(max_length=255, description="技能名称")
    description = fields.TextField(null=True, description="技能说明")
    skill_dir = fields.CharField(max_length=512, description="技能目录相对路径（相对技能根目录）")
    version = fields.IntField(default=1, description="技能版本")
    is_builtin = fields.BooleanField(default=False, description="是否内置")
    enabled = fields.BooleanField(default=True, description="是否启用")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "skill_key": self.skill_key,
            "name": self.name,
            "description": self.description,
            "skill_dir": self.skill_dir,
            "version": self.version,
            "is_builtin": self.is_builtin,
            "enabled": self.enabled,
        }

    class Meta:
        table = "digital_employee_skill"
        ordering = ["-utime"]
        unique_together = (("tenant_id", "skill_key"),)
        indexes = (("tenant_id", "is_builtin"),)


class EmployeeApiCredential(BaseModel):
    """数字员工到 AIOE 平台 API 账号的映射。

    每个数字员工在创建时派生一个 type=api 的 Account，绑定员工的 RBAC 角色，
    签发长期有效的 token 供 lion_agent 的 aioe-cli 工具回调 AIOE OpenAPI。
    """

    employee_id = fields.CharField(max_length=36, description="数字员工 CommandAgent ID")
    account_id = fields.CharField(max_length=36, description="AIOE API 账号 Account ID")
    api_token = EncryptedCharField(max_length=2048, description="aioe-auth token（account_id:jwt）")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tenant_id": self.tenant_id,
            "employee_id": self.employee_id,
            "account_id": self.account_id,
        }

    class Meta:
        table = "employee_api_credential"
        ordering = ["-utime"]
        unique_together = (("employee_id",),)
