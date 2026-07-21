# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel, EncryptedTextField

__all__ = ["NetworkTopology", "NetworkTopologyVersion"]


class NetworkTopology(BaseModel):
    """网络拓扑数据，由用户或外部剧本通过 CRUD 接口维护。"""

    name = fields.CharField(max_length=200, description="拓扑名称")
    source_config = EncryptedTextField(
        default="", description="动作采集的设备配置（加密存储）"
    )
    topology_json = fields.JSONField(
        default=dict, description="配置派生的拓扑结构（nodes/edges）"
    )
    layout_json = fields.JSONField(
        null=True, description="前端保存的节点布局（坐标信息）"
    )
    source_hash = fields.CharField(
        max_length=64, null=True, description="最近一次写入的设备配置内容哈希"
    )
    revision = fields.IntField(default=1, description="拓扑数据版本")
    last_sync_at = fields.DatetimeField(
        null=True, description="最近一次外部配置同步完成时间"
    )

    def validate(self) -> None:
        pass

    @property
    def json(self) -> dict:
        topology = self.topology_json or {}
        return {
            "id": self.id,
            "name": self.name,
            "topology_json": topology,
            "layout_json": self.layout_json,
            "node_count": len(topology.get("nodes", [])),
            "edge_count": len(topology.get("edges", [])),
            "revision": self.revision,
            "source_hash": self.source_hash,
            "last_sync_at": strip_timezone_to_seconds(self.last_sync_at),
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
        }

    class Meta:
        table = "network_topology"


class NetworkTopologyVersion(BaseModel):
    """网络拓扑不可变版本快照。"""

    topology = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="versions",
        description="所属网络拓扑",
    )
    version = fields.IntField(description="对应网络拓扑 revision")
    name = fields.CharField(max_length=200, description="拓扑名称快照")
    topology_json = fields.JSONField(default=dict, description="拓扑结构快照")
    layout_json = fields.JSONField(null=True, description="节点布局快照")
    source_hash = fields.CharField(
        max_length=64, null=True, description="设备配置内容哈希快照"
    )
    change_type = fields.CharField(max_length=32, description="变更类型")
    change_summary = fields.TextField(description="变更摘要")

    def validate(self) -> None:
        pass

    @property
    def json(self) -> dict:
        topology = self.topology_json or {}
        topology_id = getattr(self, "topology_id", None)
        if topology_id is None:
            topology_id = getattr(getattr(self, "topology", None), "id", None)
        return {
            "id": self.id,
            "version_id": self.id,
            "topology_id": topology_id,
            "version": self.version,
            "name": self.name,
            "topology_json": topology,
            "layout_json": self.layout_json,
            "source_hash": self.source_hash,
            "change_type": self.change_type,
            "change_summary": self.change_summary,
            "node_count": len(topology.get("nodes", [])),
            "edge_count": len(topology.get("edges", [])),
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
        }

    class Meta:
        table = "network_topology_version"
        ordering = ["-version"]
        unique_together = (("topology_id", "version"),)
        indexes = (("tenant_id", "topology_id"),)
