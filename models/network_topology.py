# coding=utf-8

from tortoise import fields

from models.base import BaseModel

__all__ = ["NetworkTopology"]


class NetworkTopology(BaseModel):
    """网络拓扑快照。存储原始设备配置及 AI 解析出的拓扑结构。"""

    name = fields.CharField(max_length=200, description="拓扑名称")
    source_config = fields.TextField(description="原始设备配置文本")
    topology_json = fields.JSONField(description="AI 解析出的拓扑结构（nodes/edges）")
    layout_json = fields.JSONField(null=True, description="前端保存的节点布局（坐标信息）")

    def validate(self) -> None:
        pass

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "topology_json": self.topology_json,
            "layout_json": self.layout_json,
            "ctime": str(self.ctime),
            "utime": str(self.utime),
        }

    class Meta:
        table = "network_topology"
