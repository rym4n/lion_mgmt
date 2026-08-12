"""Shared scope and frozen-state declarations for topology v2 models."""

from __future__ import annotations

from enum import Enum

from tortoise import fields

from models.base import BaseModel

__all__ = (
    "ANALYSIS_KINDS",
    "ANALYSIS_RUN_STAGES",
    "ANALYSIS_RUN_STATES",
    "AnalysisKind",
    "AnalysisRunStage",
    "AnalysisRunState",
    "MANIFEST_STATUSES",
    "ManifestStatus",
    "PAYLOAD_OBJECT_STATUSES",
    "PayloadObjectStatus",
    "UPLOAD_SESSION_STATUSES",
    "UPGRADE_INTENT_STATUSES",
    "UploadSessionStatus",
    "UpgradeIntentStatus",
    "TopologyTenantModel",
)


class ManifestStatus(str, Enum):
    ISSUED = "ISSUED"
    COMMITTED = "COMMITTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class UploadSessionStatus(str, Enum):
    OPEN = "OPEN"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"


class PayloadObjectStatus(str, Enum):
    STAGING = "STAGING"
    READY = "READY"
    LIVE = "LIVE"
    DELETING = "DELETING"


class AnalysisKind(str, Enum):
    SNAPSHOT = "SNAPSHOT"
    REANALYZE = "REANALYZE"


class AnalysisRunState(str, Enum):
    RECEIVED = "RECEIVED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    WAITING_FOR_PUBLISH = "WAITING_FOR_PUBLISH"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class AnalysisRunStage(str, Enum):
    SPLITTING = "SPLITTING"
    PARSING = "PARSING"
    VALIDATING_FACTS = "VALIDATING_FACTS"
    FACTS_READY = "FACTS_READY"
    RESOLVING_IDENTITIES = "RESOLVING_IDENTITIES"
    BUILDING_SCOPES = "BUILDING_SCOPES"
    PROJECTING_GRAPH = "PROJECTING_GRAPH"
    VALIDATING_GRAPH = "VALIDATING_GRAPH"
    PUBLISHING = "PUBLISHING"


class UpgradeIntentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


MANIFEST_STATUSES = frozenset(item.value for item in ManifestStatus)
UPLOAD_SESSION_STATUSES = frozenset(item.value for item in UploadSessionStatus)
PAYLOAD_OBJECT_STATUSES = frozenset(item.value for item in PayloadObjectStatus)
ANALYSIS_KINDS = frozenset(item.value for item in AnalysisKind)
ANALYSIS_RUN_STATES = frozenset(item.value for item in AnalysisRunState)
ANALYSIS_RUN_STAGES = frozenset(item.value for item in AnalysisRunStage)
UPGRADE_INTENT_STATUSES = frozenset(item.value for item in UpgradeIntentStatus)


class TopologyTenantModel(BaseModel):
    """Abstract base for records owned by exactly one tenant."""

    tenant_id: str = fields.CharField(max_length=255, null=False)

    class Meta:
        abstract = True
