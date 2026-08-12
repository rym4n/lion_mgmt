"""Additive topology v2 collection-side ORM declarations."""

from __future__ import annotations

from tortoise import fields

from models._network_topology_v2_base import (
    ManifestStatus,
    PayloadObjectStatus,
    TopologyTenantModel,
    UploadSessionStatus,
)
from models.base import EncryptedTextField

__all__ = (
    "TopologyCollectionManifest",
    "TopologyCollectionSnapshot",
    "TopologyObservationStaging",
    "TopologyPayloadObject",
    "TopologyProfileRevision",
    "TopologySnapshotUploadSession",
)


class TopologyProfileRevision(TopologyTenantModel):
    """Immutable topology profile revision."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_profile_revisions",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    revision = fields.BigIntField()
    resource_revision = fields.BigIntField()
    profile_json = EncryptedTextField()
    profile_hash = fields.CharField(max_length=71)
    created_by = fields.CharField(max_length=255)

    class Meta:
        table = "network_topology_profile_revision"
        indexes = (
            ("tenant_id", "topology_row_id", "revision"),
            ("tenant_id", "topology_row_id", "resource_revision"),
        )


class TopologyPayloadObject(TopologyTenantModel):
    """Tenant-isolated encrypted object metadata for immutable payloads."""

    content_hash = fields.CharField(max_length=71)
    object_key = EncryptedTextField()
    encryption_version = fields.CharField(max_length=64)
    key_version = fields.CharField(max_length=128)
    size_bytes = fields.BigIntField()
    status = fields.CharEnumField(
        PayloadObjectStatus,
        default=PayloadObjectStatus.STAGING,
    )
    reference_count = fields.BigIntField(default=0)
    verified_at = fields.DatetimeField(null=True)
    last_referenced_at = fields.DatetimeField(null=True)
    retention_until = fields.DatetimeField(null=True)

    class Meta:
        table = "network_topology_payload_object"
        indexes = (
            ("tenant_id", "content_hash"),
            ("tenant_id", "status"),
            ("tenant_id", "retention_until"),
        )


class TopologyCollectionManifest(TopologyTenantModel):
    """Issued collection contract and its eventual commit metadata."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_collection_manifests",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    profile_revision_row = fields.ForeignKeyField(
        "models.TopologyProfileRevision",
        related_name="collection_manifests",
        source_field="profile_revision_row_id",
        on_delete=fields.RESTRICT,
    )
    manifest_id = fields.CharField(max_length=255)
    manifest_hash = fields.CharField(max_length=71)
    collection_sequence = fields.BigIntField()
    base_graph_revision = fields.BigIntField()
    base_publication_revision = fields.BigIntField()
    profile_revision = fields.BigIntField()
    selection_provenance = EncryptedTextField()
    expected_resources_json = EncryptedTextField()
    capability_registry_version = fields.CharField(max_length=255)
    upgrade_intent_row = fields.ForeignKeyField(
        "models.TopologyUpgradeIntent",
        related_name="collection_manifests",
        source_field="upgrade_intent_row_id",
        null=True,
        on_delete=fields.RESTRICT,
    )
    issued_at = fields.DatetimeField()
    expires_at = fields.DatetimeField()
    consumed_at = fields.DatetimeField(null=True)
    status = fields.CharEnumField(ManifestStatus, default=ManifestStatus.ISSUED)
    creator_subject = fields.CharField(max_length=255)
    playbook_id = fields.CharField(max_length=255)
    run_id = fields.CharField(max_length=255)
    committed_snapshot_row = fields.ForeignKeyField(
        "models.TopologyCollectionSnapshot",
        related_name="committing_manifests",
        source_field="committed_snapshot_row_id",
        null=True,
        on_delete=fields.RESTRICT,
    )
    committed_snapshot_id = fields.CharField(max_length=255, null=True)
    committed_payload_hash = fields.CharField(max_length=71, null=True)
    committed_analysis_run_row = fields.ForeignKeyField(
        "models.TopologyAnalysisRun",
        related_name="committed_manifests",
        source_field="committed_analysis_run_row_id",
        null=True,
        on_delete=fields.RESTRICT,
    )

    class Meta:
        table = "network_topology_collection_manifest"
        indexes = (
            ("tenant_id", "topology_row_id", "manifest_id"),
            ("tenant_id", "topology_row_id", "collection_sequence"),
            ("tenant_id", "topology_row_id", "status"),
            ("tenant_id", "expires_at"),
        )


class TopologyCollectionSnapshot(TopologyTenantModel):
    """Snapshot metadata; admission and Migration C require exactly one payload mode."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_collection_snapshots",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    target_manifest_row = fields.OneToOneField(
        "models.TopologyCollectionManifest",
        related_name="collection_snapshots",
        source_field="target_manifest_row_id",
        on_delete=fields.RESTRICT,
    )
    profile_revision_row = fields.ForeignKeyField(
        "models.TopologyProfileRevision",
        related_name="collection_snapshots",
        source_field="profile_revision_row_id",
        on_delete=fields.RESTRICT,
    )
    payload_object_row = fields.ForeignKeyField(
        "models.TopologyPayloadObject",
        related_name="collection_snapshots",
        source_field="payload_object_row_id",
        null=True,
        on_delete=fields.RESTRICT,
    )
    redacted_payload = EncryptedTextField(null=True)
    snapshot_id = fields.CharField(max_length=255)
    schema_version = fields.CharField(max_length=255)
    collector_metadata = EncryptedTextField()
    collection_started_at = fields.DatetimeField()
    collection_ended_at = fields.DatetimeField()
    received_at = fields.DatetimeField()
    manifest_id = fields.CharField(max_length=255)
    manifest_hash = fields.CharField(max_length=71)
    collection_sequence = fields.BigIntField()
    base_graph_revision = fields.BigIntField()
    base_publication_revision = fields.BigIntField()
    profile_revision = fields.BigIntField()
    payload_hash = fields.CharField(max_length=71)
    completeness_summary = EncryptedTextField()
    status = fields.CharField(max_length=32)
    retention_until = fields.DatetimeField()
    creator_subject = fields.CharField(max_length=255)
    playbook_id = fields.CharField(max_length=255)
    run_id = fields.CharField(max_length=255)

    class Meta:
        table = "network_topology_collection_snapshot"
        indexes = (
            ("tenant_id", "topology_row_id", "snapshot_id"),
            ("tenant_id", "topology_row_id", "collection_sequence"),
            ("tenant_id", "received_at"),
            ("tenant_id", "retention_until"),
        )


class TopologySnapshotUploadSession(TopologyTenantModel):
    """Durable state for an observation-by-observation snapshot upload."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_snapshot_upload_sessions",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    manifest_row = fields.ForeignKeyField(
        "models.TopologyCollectionManifest",
        related_name="upload_sessions",
        source_field="manifest_row_id",
        on_delete=fields.RESTRICT,
    )
    committed_snapshot_row = fields.ForeignKeyField(
        "models.TopologyCollectionSnapshot",
        related_name="committed_upload_sessions",
        source_field="committed_snapshot_row_id",
        null=True,
        on_delete=fields.RESTRICT,
    )
    upload_id = fields.CharField(max_length=255)
    snapshot_id = fields.CharField(max_length=255)
    envelope_json = EncryptedTextField()
    envelope_hash = fields.CharField(max_length=71)
    status = fields.CharEnumField(
        UploadSessionStatus,
        default=UploadSessionStatus.OPEN,
    )
    client_expected_input_hash = fields.CharField(max_length=71, null=True)
    expected_observation_count = fields.BigIntField()
    authoritative_payload_hash = fields.CharField(max_length=71, null=True)
    submitter_subject = fields.CharField(max_length=255)
    playbook_id = fields.CharField(max_length=255)
    run_id = fields.CharField(max_length=255)
    expires_at = fields.DatetimeField()
    committed_at = fields.DatetimeField(null=True)
    committed_analysis_run_row = fields.ForeignKeyField(
        "models.TopologyAnalysisRun",
        related_name="committed_upload_sessions",
        source_field="committed_analysis_run_row_id",
        null=True,
        on_delete=fields.RESTRICT,
    )

    class Meta:
        table = "network_topology_snapshot_upload_session"
        indexes = (
            ("tenant_id", "topology_row_id", "upload_id"),
            ("tenant_id", "topology_row_id", "status"),
            ("status", "expires_at"),
        )


class TopologyObservationStaging(TopologyTenantModel):
    """Temporary encrypted observation staged under one upload session."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_observation_staging_rows",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    upload_session_row = fields.ForeignKeyField(
        "models.TopologySnapshotUploadSession",
        related_name="observation_staging_rows",
        source_field="upload_session_row_id",
        on_delete=fields.CASCADE,
    )
    observation_id = fields.CharField(max_length=255)
    resource_id = fields.CharField(max_length=255)
    content_hash = fields.CharField(max_length=71)
    redacted_sha256 = fields.CharField(max_length=71)
    observation_payload = EncryptedTextField()
    size_bytes = fields.BigIntField()
    received_at = fields.DatetimeField()

    class Meta:
        table = "network_topology_observation_staging"
        indexes = (
            ("tenant_id", "upload_session_row_id", "observation_id"),
            ("tenant_id", "topology_row_id", "resource_id"),
        )
