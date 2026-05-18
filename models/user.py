# coding=utf-8
import re
import uuid

from tortoise import Model, fields
from tortoise.validators import RegexValidator

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Account(BaseModel):
    """
    账号模型，包含以下属性：用户名、姓名、邮箱、手机号、密码、OAuth ID、是否禁用、角色
    """

    username: str = fields.CharField(null=False, max_length=255, unique=True)
    display_name: str = fields.CharField(max_length=255, null=True)
    email: str = fields.CharField(
        max_length=150,
        null=True,
        validators=[RegexValidator(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", re.I)],
    )
    phone: str = fields.CharField(
        max_length=15,
        null=True,
        validators=[RegexValidator(r"^1[3-9]\d{9}$", re.I)],
    )  # 中国
    password: str = fields.CharField(max_length=255)
    mfa_enable: bool = fields.BooleanField(default=False)
    mfa_key: str = fields.CharField(max_length=50, null=True)
    mfa_bind: bool = fields.BooleanField(default=False)
    oauth_id: str = fields.CharField(null=True, max_length=255)
    disabled: bool = fields.BooleanField(default=False)
    roles = fields.ManyToManyField("models.Role", through="account_role")
    permit_ip: str = fields.CharField(null=True, max_length=255)
    type: str = fields.CharField(max_length=255, default="user")
    creator = fields.CharField(max_length=32, null=True, description="创建人")
    expiration = fields.DatetimeField(null=True, description="Token过期时间")
    last_login_time = fields.DatetimeField(null=True, description="最后登录时间")
    star_rooms = fields.ManyToManyField("models.Room", through="account_star_room")

    def _roles_for_tenant(self, tenant_id: str | None):
        """仅返回属于指定租户(tenant_id)的角色；tenant_id 为 None 时不做过滤。"""
        if not self.roles:
            return []
        roles_list = list(self.roles)
        if tenant_id is None:
            return roles_list
        return [r for r in roles_list if r.tenant_id == tenant_id]

    def to_json(self, tenant_id: str | None = None) -> dict:
        workspace_roles = self._roles_for_tenant(tenant_id)
        tmp = {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "email": self.email,
            "username": self.username,
            "type": self.type,
            "display_name": self.display_name,
            "phone": self.phone,
            "disabled": self.disabled,
            "oauth_id": self.oauth_id,
            "last_login_time": strip_timezone_to_seconds(self.last_login_time),
            "mfa_enable": self.mfa_enable,
            "mfa_bind": self.mfa_bind,
            "permit_ip": self.permit_ip,
            "roles": [role.name for role in workspace_roles],
            "permissions": [],
        }
        for role in workspace_roles:
            tmp["permissions"].extend(role.permissions or [])
        return tmp

    @property
    def json(self) -> dict:
        return self.to_json(None)

    def to_api_json(self, tenant_id: str | None = None) -> dict:
        workspace_roles = self._roles_for_tenant(tenant_id)
        tmp = {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "username": self.username,
            "description": self.display_name,
            "permit_ip": self.permit_ip,
            "roles": [role.name for role in workspace_roles],
            "creator": self.creator,
            "expiration": strip_timezone_to_seconds(self.expiration),
        }

        return tmp

    @property
    def api_json(self) -> dict:
        return self.to_api_json(None)

    class Meta:
        table = "account"
        ordering = ["-utime"]


class Role(BaseModel):
    """
    角色权限
    """

    name: str = fields.CharField(max_length=255)
    description: str = fields.CharField(max_length=255, null=True)
    permissions: list[str] = fields.JSONField(null=True)

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "description": self.description,
            "permissions": self.permissions,
        }

    class Meta:
        table = "role"
        ordering = ["-utime"]


class PermissionsMenu(BaseModel):
    """
    权限菜单
    """

    model: str = fields.CharField(unique=True, max_length=255)
    description: str = fields.CharField(max_length=255, null=True)
    Permissions: list[str] = fields.JSONField(null=True)
    url = fields.JSONField(null=True)
    bp = fields.CharField(max_length=32, null=True)

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "model": self.model,
            "description": self.description,
            "permissions": self.Permissions,
        }

    class Meta:
        table = "permissions_menu"
        ordering = ["-utime"]


class Tenant(Model):
    """
    租户管理
    """

    id: str = fields.CharField(pk=True, max_length=36, default=lambda: str(uuid.uuid4()))
    ctime = fields.DatetimeField(null=False, auto_now_add=True, description="创建时间")
    utime = fields.DatetimeField(null=False, auto_now=True, description="更新时间")
    deleted: bool = fields.BooleanField(default=False, description="删除标识")
    name: str = fields.CharField(max_length=255, null=True, description="租户名称")
    creator = fields.ForeignKeyField("models.Account", null=True, related_name="creator_tenant", description="创建人")
    member = fields.ManyToManyField(
        model_name="models.Account",
        related_name="member",
        through="tenant_member",
        description="租户成员",
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
            "creator": (
                {
                    "username": self.creator.username,
                    "display_name": self.creator.display_name,
                }
                if self.creator
                else {}
            ),
        }

    class Meta:
        table = "tenant"
        ordering = ["-utime"]
