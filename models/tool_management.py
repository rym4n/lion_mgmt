# coding=utf-8
import orjson
from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models import BaseModel, EncryptedCharField


class ToolAction(BaseModel):
    """
    工具动作管理表
    """

    tool_name: str = fields.CharField(max_length=255, null=True, description="工具名称")
    version_number: str = fields.CharField(max_length=50, null=True, description="版本号")
    action = fields.JSONField(null=True, description="动作")
    playbook: list[str] = fields.JSONField(null=True, description="绑定的剧本列表")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tool_name": self.tool_name,
            "version_number": self.version_number,
            "action": self.action,
            "playbook": self.playbook,
        }

    class Meta:
        table = "tool_action"
        ordering = ["-utime"]


class Tool(BaseModel):
    """
    工具管理表
    """

    name: str = fields.CharField(max_length=255, null=True, description="工具名称")
    version: str = fields.CharField(max_length=50, null=True, description="工具版本号")
    path: str = fields.CharField(max_length=255, null=True, description="文件地址")
    logo_path: str = fields.CharField(max_length=255, null=True, description="Logo地址")
    description: str = fields.CharField(max_length=255, null=True, description="工具描述")
    status: int = fields.IntField(default=0, description="是否有剧本使用它 1:有 0:无")
    creator: str = fields.CharField(max_length=255, null=True, description="创建者")
    updater: str = fields.CharField(max_length=255, null=True, description="更新者")
    hash_value: str = fields.CharField(max_length=255, null=True, description="文件hash值")
    internal: bool = fields.BooleanField(default=False, description="是否支持下载")
    maintain: str = fields.BooleanField(default=False, description="是否内置应用")
    tag: list[str] = fields.JSONField(null=True, description="工具标签")
    main: str = fields.CharField(max_length=255, null=True, description="入口文件")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "status": self.status,
            "creator": self.creator,
            "updater": self.updater,
            "hash_value": self.hash_value,
            "internal": self.internal,
            "tag": self.tag,
            "main": self.main,
        }

    class Meta:
        table = "tool"
        ordering = ["-utime"]


class ToolResourcesModel(BaseModel):
    """
    工具资源模板表
    """

    tool_name: str = fields.CharField(max_length=255, description="工具名称")
    version: str = fields.CharField(max_length=255, description="支持健康检查的工具版本", null=True)
    resources: list[str] = fields.JSONField(description="资源模板")
    status: int = fields.IntField(default=0, description="是否必填 1:必填 0:非必填")
    health_check: int = fields.IntField(default=0, description="是否支持健康检查 1:支持 0:不支持")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tool_name": self.tool_name,
            "resources": self.resources,
            "status": self.status,
        }

    class Meta:
        table = "tool_resources_model"
        ordering = ["-utime"]


class ToolResources(BaseModel):
    """
    工具资源表
    """

    tool_name: str = fields.CharField(max_length=255, description="工具名称")
    name: str = fields.CharField(max_length=255, description="资源名称")
    engine: str = fields.CharField(max_length=255, null=True, description="执行引擎")
    resources = EncryptedCharField(max_length=5000, null=False, description="资源配置")
    internal: bool = fields.BooleanField(default=False, description="是否为维护模式资源")
    creator: str = fields.CharField(max_length=255, null=True, description="创建者")
    updater: str = fields.CharField(max_length=255, null=True, description="更新者")
    status: str = fields.CharField(max_length=32, null=True, description="健康状态")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        resources_data = None
        if self.resources:
            if isinstance(self.resources, str):
                try:
                    resources_data = orjson.loads(self.resources)
                except orjson.JSONDecodeError:
                    resources_data = None
            elif isinstance(self.resources, dict):
                resources_data = self.resources.copy()

            if resources_data and isinstance(resources_data, dict):
                for value in resources_data.values():
                    if isinstance(value, dict) and value.get("data_type") == "password":
                        value["value"] = "******"

        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "tool_name": self.tool_name,
            "name": self.name,
            "resources": resources_data,
            "creator": self.creator,
            "updater": self.updater,
            "engine": self.engine,
            "status": self.status,
        }

    class Meta:
        table = "tool_resources"
        ordering = ["-utime"]


class ActionPermissions(BaseModel):
    """
    动作权限表
    """

    action_name: str = fields.CharField(max_length=255, description="动作名称")
    action_description: str = fields.CharField(max_length=255, description="动作描述")
    tool_name: str = fields.CharField(max_length=255, description="工具名称")
    roles = fields.JSONField(default=[], description="有权限执行的角色")
    users = fields.JSONField(default=[], description="有权限执行的用户")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "action_name": self.action_name,
            "action_description": self.action_description,
            "tool_name": self.tool_name,
            "users_id": self.users,
            "roles_id": self.roles,
        }

    class Meta:
        table = "action_permissions"
        ordering = ["-utime"]
