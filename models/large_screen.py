# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel


class LargeScreen(BaseModel):
    """
    大屏配置模型，保存大屏名称与大屏 JSON 数据。
    """

    name = fields.CharField(max_length=255, index=True, description="大屏名称")
    screen_data = fields.JSONField(default=dict, description="大屏数据")

    def validate(self) -> None:
        raise NotImplementedError

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
            "name": self.name,
            "screen_data": self.screen_data,
        }

    class Meta:
        table = "large_screen"
        ordering = ["-utime"]
        indexes = (("tenant_id", "deleted"), ("tenant_id", "name"))
