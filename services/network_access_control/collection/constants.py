"""Stable names shared by the network policy collection modules."""

BUILTIN_PLAYBOOK_NAME = "网络策略自动采集"
TIMER_PREFIX = "network_policy_collection_"
LOCK_NAMESPACE = "network_policy_collection"
MAX_RESOURCES = 500
AUTO_OPERATOR = "auto_collection"
SYNC_SOURCE = "auto_collection"
# 可绑定的网络设备资产模型（与 lion_web NETWORK_DEVICE_MODEL_KEYS 一致）
POLICY_DEVICE_MODEL_KEYS = frozenset({"firewall", "switch", "router", "security_device"})
