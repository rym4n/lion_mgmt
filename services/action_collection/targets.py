"""Freeze tenant-scoped action and resource identities before collection."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    item_id: str
    app_name: str
    app_version: str
    main: str
    action_func: str
    action_name: str
    action_input: dict
    resource_id: str
    resource_name: str
    host: str | None
    asset_instance_id: str | None
