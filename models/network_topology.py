# coding=utf-8

from tortoise import fields

from libs.utils import strip_timezone_to_seconds
from models.base import BaseModel, EncryptedTextField

__all__ = ["NetworkTopology"]


class NetworkTopology(BaseModel):
    """手工绘制或由应用动作采集配置后自动生成的网络拓扑。"""

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
    action_sources = fields.JSONField(default=list, description="应用动作数据源绑定")
    refresh_interval = fields.IntField(default=3600, description="自动刷新周期（秒）")
    enabled = fields.BooleanField(default=True, description="是否启用自动刷新")
    executor_id = fields.CharField(
        max_length=36, null=True, description="自动采集执行账户ID"
    )
    source_hash = fields.CharField(
        max_length=64, null=True, description="最近成功配置内容哈希"
    )
    revision = fields.IntField(default=1, description="数据源定义版本")
    last_run_ids = fields.JSONField(default=list, description="最近一次采集运行ID")
    refresh_status = fields.CharField(
        max_length=32, default="pending", description="刷新状态"
    )
    last_refresh_at = fields.DatetimeField(
        null=True, description="最近一次刷新完成时间"
    )
    next_refresh_at = fields.DatetimeField(null=True, description="下次刷新时间")
    refresh_started_at = fields.DatetimeField(null=True, description="本次刷新开始时间")
    refresh_token = fields.CharField(
        max_length=36, null=True, description="刷新租约令牌"
    )
    refresh_error = fields.TextField(null=True, description="最近一次刷新错误")

    def validate(self) -> None:
        pass

    @property
    def json(self) -> dict:
        topology = self.topology_json or {}
        mode = "automatic" if self.action_sources else "manual"
        return {
            "id": self.id,
            "name": self.name,
            "mode": mode,
            "source_type": mode,
            "topology_json": topology,
            "layout_json": self.layout_json,
            "source_count": len(self.action_sources or []),
            "node_count": len(topology.get("nodes", [])),
            "edge_count": len(topology.get("edges", [])),
            "refresh_interval": self.refresh_interval,
            "enabled": self.enabled,
            "revision": self.revision,
            "last_run_ids": self.last_run_ids or [],
            "refresh_status": self.refresh_status,
            "last_refresh_at": strip_timezone_to_seconds(self.last_refresh_at),
            "next_refresh_at": strip_timezone_to_seconds(self.next_refresh_at),
            "refresh_error": self.refresh_error,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "utime": strip_timezone_to_seconds(self.utime),
        }

    class Meta:
        table = "network_topology"
