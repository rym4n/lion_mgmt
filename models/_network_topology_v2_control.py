"""Additive topology v2 rollout, upgrade, alert, and outbox declarations."""

from __future__ import annotations

from tortoise import fields

from models._network_topology_v2_base import TopologyTenantModel, UpgradeIntentStatus
from models.base import EncryptedTextField

__all__ = ("TopologySyncAlert", "TopologyUpgradeIntent")


class TopologyUpgradeIntent(TopologyTenantModel):
    """Persistent authorization to replace one exact legacy graph revision."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_upgrade_intents",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    legacy_graph_version_row = fields.ForeignKeyField(
        "models.NetworkTopologyVersion",
        related_name="v2_upgrade_intents",
        source_field="legacy_graph_version_row_id",
        on_delete=fields.RESTRICT,
    )
    expected_legacy_revision = fields.BigIntField()
    status = fields.CharEnumField(
        UpgradeIntentStatus,
        default=UpgradeIntentStatus.ACTIVE,
    )
    created_by = fields.CharField(max_length=255)
    expires_at = fields.DatetimeField()
    consumed_at = fields.DatetimeField(null=True)
    consumed_by = fields.CharField(max_length=255, null=True)
    cancelled_at = fields.DatetimeField(null=True)
    cancelled_by = fields.CharField(max_length=255, null=True)
    cancellation_reason = EncryptedTextField(null=True)

    class Meta:
        table = "network_topology_upgrade_intent"
        indexes = (
            ("tenant_id", "topology_row_id", "status"),
            ("tenant_id", "expires_at"),
        )


class TopologySyncAlert(TopologyTenantModel):
    """Idempotent alert lifecycle for one topology synchronization."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_sync_alerts",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    analysis_run_row = fields.ForeignKeyField(
        "models.TopologyAnalysisRun",
        related_name="sync_alerts",
        source_field="analysis_run_row_id",
        null=True,
        on_delete=fields.RESTRICT,
    )
    sync_id = fields.CharField(max_length=255)
    alert_type = fields.CharField(max_length=128)
    severity = fields.CharField(max_length=32)
    status = fields.CharField(max_length=32, default="OPEN")
    owner_subject = fields.CharField(max_length=255, null=True)
    details = EncryptedTextField()
    opened_at = fields.DatetimeField()
    closed_at = fields.DatetimeField(null=True)
    closed_by = fields.CharField(max_length=255, null=True)

    class Meta:
        table = "network_topology_sync_alert"
        indexes = (
            ("tenant_id", "sync_id", "alert_type"),
            ("tenant_id", "topology_row_id", "status"),
        )

