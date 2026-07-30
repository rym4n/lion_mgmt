"""Additive topology v2 layout and presentation declarations."""

from __future__ import annotations

from tortoise import fields

from models._network_topology_v2_base import TopologyTenantModel
from models.base import EncryptedTextField

__all__ = (
    "PresentationOverride",
    "TopologyLayoutSet",
    "TopologyScopeLayoutVersion",
)


class TopologyScopeLayoutVersion(TopologyTenantModel):
    """Immutable layout revision for one graph scope."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_scope_layout_versions",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    scope_id = fields.CharField(max_length=255)
    scope_layout_revision = fields.BigIntField()
    graph_revision = fields.BigIntField()
    layout_json = EncryptedTextField()
    viewport_json = EncryptedTextField()
    created_by = fields.CharField(max_length=255)

    class Meta:
        table = "network_topology_scope_layout_version"
        indexes = (
            (
                "tenant_id",
                "topology_row_id",
                "scope_id",
                "scope_layout_revision",
            ),
            ("tenant_id", "topology_row_id", "graph_revision"),
        )


class TopologyLayoutSet(TopologyTenantModel):
    """Immutable copy-on-write map of scope IDs to layout versions."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_layout_sets",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    layout_set_revision = fields.BigIntField()
    scope_version_map_json = EncryptedTextField()
    created_by = fields.CharField(max_length=255)

    class Meta:
        table = "network_topology_layout_set"
        indexes = (("tenant_id", "topology_row_id", "layout_set_revision"),)


class PresentationOverride(TopologyTenantModel):
    """Manual presentation-only override that never mutates observed facts."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_presentation_overrides",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    profile_revision_row = fields.ForeignKeyField(
        "models.TopologyProfileRevision",
        related_name="presentation_overrides",
        source_field="profile_revision_row_id",
        on_delete=fields.RESTRICT,
    )
    target_stable_id = fields.CharField(max_length=255)
    override_kind = fields.CharField(max_length=64)
    override_value = EncryptedTextField()
    status = fields.CharField(max_length=32, default="ACTIVE")
    created_by = fields.CharField(max_length=255)
    orphaned_at = fields.DatetimeField(null=True)
    migrated_to_target_stable_id = fields.CharField(max_length=255, null=True)

    class Meta:
        table = "network_topology_presentation_override"
        indexes = (
            ("tenant_id", "topology_row_id", "target_stable_id", "status"),
            ("tenant_id", "topology_row_id", "profile_revision_row_id"),
        )
