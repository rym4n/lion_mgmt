"""Additive topology v2 analysis-run ORM declaration."""

from __future__ import annotations

from tortoise import fields

from models._network_topology_v2_base import (
    AnalysisKind,
    AnalysisRunStage,
    AnalysisRunState,
    TopologyTenantModel,
)
from models.base import EncryptedTextField

__all__ = ("TopologyAnalysisRun",)


class TopologyAnalysisRun(TopologyTenantModel):
    """Durable parser, validation, lease, and publication-attempt state."""

    topology_row = fields.ForeignKeyField(
        "models.NetworkTopology",
        related_name="v2_analysis_runs",
        source_field="topology_row_id",
        on_delete=fields.RESTRICT,
    )
    snapshot_row = fields.ForeignKeyField(
        "models.TopologyCollectionSnapshot",
        related_name="analysis_runs",
        source_field="snapshot_row_id",
        on_delete=fields.RESTRICT,
    )
    target_profile_revision_row = fields.ForeignKeyField(
        "models.TopologyProfileRevision",
        related_name="analysis_runs",
        source_field="target_profile_revision_row_id",
        on_delete=fields.RESTRICT,
    )
    fact_graph_payload_object_row = fields.ForeignKeyField(
        "models.TopologyPayloadObject",
        related_name="fact_graph_analysis_runs",
        source_field="fact_graph_payload_object_row_id",
        null=True,
        on_delete=fields.RESTRICT,
    )
    validation_report_object_row = fields.ForeignKeyField(
        "models.TopologyPayloadObject",
        related_name="validation_report_analysis_runs",
        source_field="validation_report_object_row_id",
        null=True,
        on_delete=fields.RESTRICT,
    )
    submitter_subject = fields.CharField(max_length=255)
    playbook_id = fields.CharField(max_length=255)
    run_id = fields.CharField(max_length=255)
    analysis_kind = fields.CharEnumField(AnalysisKind)
    publication_attempt_id = fields.CharField(max_length=255)
    publish_as_current = fields.BooleanField(default=False)
    expected_publication_revision = fields.BigIntField()
    expected_current_graph_revision = fields.BigIntField()
    parser_bundle_version = fields.CharField(max_length=255)
    model_provider = fields.CharField(max_length=255, null=True)
    model_name = fields.CharField(max_length=255, null=True)
    prompt_version = fields.CharField(max_length=255, null=True)
    input_schema_version = fields.CharField(max_length=255)
    facts_schema_version = fields.CharField(max_length=255)
    graph_schema_version = fields.CharField(max_length=255)
    redaction_version = fields.CharField(max_length=255)
    deterministic_parser_versions = EncryptedTextField()
    fact_graph_payload = EncryptedTextField(null=True)
    validation_report = EncryptedTextField(null=True)
    facts_hash = fields.CharField(max_length=71, null=True)
    graph_hash = fields.CharField(max_length=71, null=True)
    graph_payload = EncryptedTextField(null=True)
    agent_observation_ids = fields.JSONField(null=True)
    state = fields.CharEnumField(AnalysisRunState, default=AnalysisRunState.RECEIVED)
    stage = fields.CharEnumField(AnalysisRunStage, default=AnalysisRunStage.SPLITTING)
    blocked_by = fields.CharField(max_length=64, null=True)
    error_code = fields.CharField(max_length=128, null=True)
    error_summary = EncryptedTextField(null=True)
    attempt_count = fields.BigIntField(default=0)
    max_attempts = fields.BigIntField(default=3)
    next_retry_at = fields.DatetimeField(null=True)
    lease_owner = fields.CharField(max_length=255, null=True)
    lease_expires_at = fields.DatetimeField(null=True)
    heartbeat_at = fields.DatetimeField(null=True)
    lease_generation = fields.BigIntField(default=0)
    fencing_token = fields.BigIntField(default=0)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)
    retryable = fields.BooleanField(default=True)
    cancellation_requested = fields.BooleanField(default=False)
    cancellation_requested_by = fields.CharField(max_length=255, null=True)
    cancellation_requested_at = fields.DatetimeField(null=True)
    cancellation_reason = EncryptedTextField(null=True)

    class Meta:
        table = "network_topology_analysis_run"
        indexes = (
            ("state", "next_retry_at", "lease_expires_at"),
            ("tenant_id", "topology_row_id", "state"),
            ("tenant_id", "snapshot_row_id", "ctime"),
            ("tenant_id", "topology_row_id", "publication_attempt_id"),
        )
