# coding=utf-8

from tortoise import fields

from models import BaseModel


class ToolVersionNumber(BaseModel):
    """
    工具版本生成序列号管理表
    """

    tool_name: str = fields.CharField(max_length=255, null=True, description="工具名称")
    upload_time: str = fields.CharField(max_length=50, null=True, description="上传时间")
    number_of_uploads: int = fields.IntField(null=True, description="上传次数")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "tool_name": self.tool_name,
            "upload_time": self.upload_time,
            "number_of_uploads": self.number_of_uploads
        }

    class Meta:
        table = "tool_version_number"


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
