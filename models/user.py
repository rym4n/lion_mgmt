# coding=utf-8
import re

from tortoise import fields
from tortoise.validators import RegexValidator

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class Account(BaseModel):
    """
    账号模型，包含以下属性：用户名、姓名、邮箱、手机号、密码、OAuth ID、是否禁用、角色
    """

    username: str = fields.CharField(unique=True, null=False, max_length=32)
    display_name: str = fields.CharField(max_length=255, null=True)
    email: str = fields.CharField(
        unique=True,
        max_length=150,
        null=True,
        validators=[
            RegexValidator(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", re.I)
        ]
    )
    phone: str = fields.CharField(
        unique=True,
        max_length=15,
        null=True,
        validators=[
            RegexValidator(r"^1[3-9]\d{9}$", re.I)  # 中国
        ]
    )
    password: str = fields.CharField(max_length=255)
    mfa_enable: bool = fields.BooleanField(default=False)
    mfa_key: str = fields.CharField(max_length=50, null=True)
    mfa_bind: bool = fields.BooleanField(default=False)
    oauth_id: str = fields.CharField(unique=True, null=True, max_length=255)
    disabled: bool = fields.BooleanField(default=False)
    roles = fields.ManyToManyField("models.Role", through="account_role")
    permit_ip: str = fields.CharField(null=True, max_length=255)
    type: str = fields.CharField(max_length=255, default="user")
    creator = fields.CharField(max_length=32, null=True, description="创建人")
    expiration = fields.DatetimeField(null=True, description="Token过期时间")
    star_rooms = fields.ManyToManyField("models.Room", through="account_star_room")

    @property
    def json(self) -> dict:
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
            "mfa_enable": self.mfa_enable,
            "mfa_bind": self.mfa_bind,
            "permit_ip": self.permit_ip,
            "roles": [role.name for role in self.roles] if self.roles else [],
            "permissions": []
        }
        for role in self.roles:
            tmp['permissions'].extend(role.permissions)
        return tmp

    @property
    def api_json(self) -> dict:
        tmp = {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "username": self.username,
            "description": self.display_name,
            "permit_ip": self.permit_ip,
            "roles": [role.name for role in self.roles] if self.roles else [],
            "creator": self.creator,
            "expiration": strip_timezone_to_seconds(self.expiration)
        }

        return tmp

    class Meta:
        table = "account"
        ordering = ["-utime"]


class Role(BaseModel):
    """
    角色权限
    """

    name: str = fields.CharField(unique=True, max_length=255)
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
    dp = fields.CharField(max_length=32, null=True)

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
