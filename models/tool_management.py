# coding=utf-8

from tortoise import fields

from models import BaseModel


class ToolAction(BaseModel):
    """
    工具动作管理表
    """

    tool_name: str = fields.CharField(max_length=255, null=True, description="工具名称")
    version_number: str = fields.CharField(max_length=50, null=True, description="版本号")
    action: list[str] = fields.JSONField(null=True, description="动作")
    roles_id: list[str] = fields.JSONField(null=True, description="有权限执行的角色")
    users_id: list[str] = fields.JSONField(null=True, description="有权限执行的用户")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "tool_name": self.tool_name,
            "version_number": self.version_number,
            "action": self.action,
            "roles_id": self.roles_id,
            "users_id": self.users_id
        }

    class Meta:
        table = "tool_action"


class Tool(BaseModel):
    """
    工具管理表
    """

    name: str = fields.CharField(max_length=255, null=True, description="工具名称")
    version: str = fields.CharField(max_length=50, null=True, description="工具版本号")
    path: str = fields.CharField(max_length=255, null=True, description="文件地址")
    description: str = fields.CharField(max_length=255, null=True, description="工具描述")
    status: int = fields.IntField(default=0, description="是否有剧本使用它 1:有 0:无")
    creator: str = fields.CharField(max_length=255, null=True, description="创建者")
    updater: str = fields.CharField(max_length=255, null=True, description="更新者")
    hash_value: str = fields.CharField(max_length=255, null=True, description="文件hash值")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "name": self.name,
            "version": self.version,
            "path": self.path,
            "description": self.description,
            "status": self.status,
            "creator": self.creator,
            "updater": self.updater,
            "hash_value": self.hash_value
        }

    class Meta:
        table = "tool"


class ToolResourcesModel(BaseModel):
    """
    工具资源模板表
    """

    tool_name: str = fields.CharField(max_length=255, description="工具名称")
    resources: list[str] = fields.JSONField(description="资源模板")


    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "tool_name": self.tool_name,
            "resources": self.resources,
        }

    class Meta:
        table = "tool_resources_model"


class ToolResources(BaseModel):
    """
    工具资源表
    """

    tool_name: str = fields.CharField(max_length=255, description="工具名称")
    name: str = fields.CharField(max_length=255, unique=True,description="资源名称")
    description: str = fields.CharField(max_length=255, description="资源描述")
    product_supplier: str = fields.CharField(max_length=255, null=True, description="供应商")
    product_name: str = fields.CharField(max_length=255, null=True, description="产品名称")
    resources: list[str] = fields.JSONField(description="资源配置")
    creator: str = fields.CharField(max_length=255, null=True, description="创建者")
    updater: str = fields.CharField(max_length=255, null=True, description="更新者")


    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "tool_name": self.tool_name,
            "name": self.name,
            "description": self.description,
            "product_supplier": self.product_supplier,
            "product_name": self.product_name,
            "resources": self.resources,
            "creator": self.creator,
            "updater": self.updater
        }

    class Meta:
        table = "tool_resources"