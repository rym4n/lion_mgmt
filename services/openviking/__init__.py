# coding=utf-8
"""OpenViking 集成服务。"""

from .client import OpenVikingAdminClient
from .lifecycle import (
    deprovision_tenant_knowledge,
    provision_tenant_knowledge,
    restore_tenant_knowledge,
    rotate_tenant_knowledge_key,
)

__all__ = [
    "OpenVikingAdminClient",
    "provision_tenant_knowledge",
    "deprovision_tenant_knowledge",
    "restore_tenant_knowledge",
    "rotate_tenant_knowledge_key",
]
