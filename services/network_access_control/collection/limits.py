"""Per-device rule limit for automatic policy collection (system config)."""
from __future__ import annotations

from models.config import Config
from models.base import unwrap_encrypted_value

MAX_RULES_CONFIG_KEY = "network_policy_collection_max_rules_per_device"
DEFAULT_MAX_RULES = 20000
_UPPER_BOUND = 100000


async def max_rules_per_device() -> int:
    row = await Config.filter(key=MAX_RULES_CONFIG_KEY, deleted=False).first()
    if row is None:
        return DEFAULT_MAX_RULES
    try:
        value = int(str(unwrap_encrypted_value(row.value)).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_RULES
    return value if 1 <= value <= _UPPER_BOUND else DEFAULT_MAX_RULES
