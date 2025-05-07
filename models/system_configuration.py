# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models import BaseModel


class SystemCustomConfiguration(BaseModel):
    """
    系统自定义配置
    """

    background: str = fields.CharField(max_length=255, null=True, description="登录背景")
    webico: str = fields.CharField(max_length=255, null=True, description="网页ico")
    logo: str = fields.CharField(max_length=255, null=True, description="系统logo")
    copyright: str = fields.CharField(max_length=255, null=True, description="版权")
    system_name: str = fields.CharField(max_length=255, null=True, description="系统名")
    system_version: str = fields.CharField(max_length=255, null=True, description="系统版本")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "system_name": self.system_name,
            "system_version": self.system_version,
            "copyright": self.copyright,
            "background": self.background,
            "webico": self.webico,
            "logo": self.logo,
        }

    class Meta:
        table = "system_custom_configuration"
        ordering = ["-utime"]


class SystemStatus(BaseModel):
    """
    系统状态表
    """

    systemname: str = fields.CharField(max_length=255, null=True, description="系统名")
    instancename: str = fields.CharField(max_length=255, null=True, description="实例名")
    uptime: str = fields.CharField(max_length=255, null=True, description="启动时间")
    checkTime: str = fields.CharField(max_length=255, null=True, description="检查时间")
    status: str = fields.CharField(max_length=255, null=True, description="状态")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "systemname": self.systemname,
            "instancename": self.instancename,
            "uptime": self.uptime,
            "checkTime": self.checkTime,
            "status": self.status
        }

    class Meta:
        table = "system_status"
        ordering = ["-utime"]
