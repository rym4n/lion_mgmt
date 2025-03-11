# coding=utf-8

from tortoise import fields

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
            "ctime": str(self.ctime),
            "utime": str(self.utime),
            "system_name": self.system_name,
            "system_version": self.system_version,
            "copyright": self.copyright,
            "background": self.background,
            "webico": self.webico,
            "logo": self.logo,
        }

    class Meta:
        table = "system_custom_configuration"
