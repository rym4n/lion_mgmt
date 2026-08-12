"""Additive topology v2 identity-registry declarations."""

from __future__ import annotations

from tortoise import fields

from models._network_topology_v2_base import TopologyTenantModel
from models.base import EncryptedTextField

__all__ = (
    "TopologyIdentityAlias",
    "TopologyIdentityConflictAudit",
    "TopologyIdentityRegistry",
)


class TopologyIdentityRegistry(TopologyTenantModel):
    """Stable canonical identity retained across analysis runs."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_identity_registry_rows",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    first_seen_analysis_run_row = fields.ForeignKeyField(
        "models.TopologyAnalysisRun",
        related_name="first_seen_identities",
        source_field="first_seen_analysis_run_row_id",
        on_delete=fields.RESTRICT,
    )
    last_seen_analysis_run_row = fields.ForeignKeyField(
        "models.TopologyAnalysisRun",
        related_name="last_seen_identities",
        source_field="last_seen_analysis_run_row_id",
        on_delete=fields.RESTRICT,
    )
    canonical_id = fields.CharField(max_length=255)
    entity_kind = fields.CharField(max_length=64)
    canonical_fingerprint = fields.CharField(max_length=71)
    status = fields.CharField(max_length=32, default="ACTIVE")
    created_by = fields.CharField(max_length=255)

    class Meta:
        table = "network_topology_identity_registry"
        indexes = (
            ("tenant_id", "topology_row_id", "canonical_id"),
            ("tenant_id", "topology_row_id", "entity_kind", "status"),
        )


class TopologyIdentityAlias(TopologyTenantModel):
    """Encrypted alias value and deterministic lookup hash for one identity."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_identity_aliases",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    identity_row = fields.ForeignKeyField(
        "models.TopologyIdentityRegistry",
        related_name="aliases",
        source_field="identity_row_id",
        on_delete=fields.RESTRICT,
    )
    first_seen_analysis_run_row = fields.ForeignKeyField(
        "models.TopologyAnalysisRun",
        related_name="first_seen_identity_aliases",
        source_field="first_seen_analysis_run_row_id",
        on_delete=fields.RESTRICT,
    )
    last_seen_analysis_run_row = fields.ForeignKeyField(
        "models.TopologyAnalysisRun",
        related_name="last_seen_identity_aliases",
        source_field="last_seen_analysis_run_row_id",
        on_delete=fields.RESTRICT,
    )
    alias_type = fields.CharField(max_length=64)
    alias_namespace = fields.CharField(max_length=255)
    alias_value = EncryptedTextField()
    alias_value_hash = fields.CharField(max_length=71)
    status = fields.CharField(max_length=32, default="ACTIVE")

    class Meta:
        table = "network_topology_identity_alias"
        indexes = (
            (
                "tenant_id",
                "topology_row_id",
                "alias_type",
                "alias_namespace",
                "alias_value_hash",
            ),
            ("tenant_id", "identity_row_id", "status"),
        )


class TopologyIdentityConflictAudit(TopologyTenantModel):
    """Fail-closed record of an alias collision and its resolution."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_identity_conflict_audits",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    analysis_run_row = fields.ForeignKeyField(
        "models.TopologyAnalysisRun",
        related_name="identity_conflict_audits",
        source_field="analysis_run_row_id",
        on_delete=fields.RESTRICT,
    )
    incumbent_identity_row = fields.ForeignKeyField(
        "models.TopologyIdentityRegistry",
        related_name="incumbent_conflict_audits",
        source_field="incumbent_identity_row_id",
        on_delete=fields.RESTRICT,
    )
    candidate_identity_row = fields.ForeignKeyField(
        "models.TopologyIdentityRegistry",
        related_name="candidate_conflict_audits",
        source_field="candidate_identity_row_id",
        null=True,
        on_delete=fields.RESTRICT,
    )
    alias_type = fields.CharField(max_length=64)
    alias_namespace = fields.CharField(max_length=255)
    alias_value_hash = fields.CharField(max_length=71)
    status = fields.CharField(max_length=32, default="UNRESOLVED")
    conflict_details = EncryptedTextField()
    resolved_by = fields.CharField(max_length=255, null=True)
    resolved_at = fields.DatetimeField(null=True)
    resolution = EncryptedTextField(null=True)

    class Meta:
        table = "network_topology_identity_conflict_audit"
        indexes = (
            ("tenant_id", "topology_row_id", "status"),
            (
                "tenant_id",
                "topology_row_id",
                "alias_type",
                "alias_namespace",
                "alias_value_hash",
            ),
        )
