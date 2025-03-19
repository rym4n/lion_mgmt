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
            "action": self.action
        }

    class Meta:
        table = "tool_action"


class Tool(BaseModel):
    """
    工具管理表
    """

    tool_name: str = fields.CharField(max_length=255, null=True, description="工具名称")
    tool_version: str = fields.CharField(max_length=50, null=True, description="工具版本号")
    tool_path: str = fields.CharField(max_length=255, null=True, description="文件地址")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "tool_path": self.tool_path
        }

    class Meta:
        table = "tool"
