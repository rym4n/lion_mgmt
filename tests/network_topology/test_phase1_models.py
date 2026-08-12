"""Static ORM declaration proof only; no DDL or database behavior is exercised."""

from __future__ import annotations

import subprocess
import sys

from tortoise import fields
from tortoise.fields.relational import (
    BackwardFKRelation,
    BackwardOneToOneRelation,
    ForeignKeyFieldInstance,
)

from models import _network_topology_v2_analysis as analysis
from models import _network_topology_v2_base as base
from models import _network_topology_v2_collection as collection
from models import _network_topology_v2_identity as identity
from models import network_topology_v2 as public_v2
from models.network_topology import NetworkTopologyVersion
from models.base import EncryptedTextField

BASE_FIELDS = {"id", "ctime", "utime", "deleted", "tenant_id"}


def _declared_fields(model) -> dict[str, object]:
    fields_map = model._meta.fields_map
    generated_sources = {
        field.source_field
        for field in fields_map.values()
        if isinstance(field, ForeignKeyFieldInstance)
    }
    reverse_types = (BackwardFKRelation, BackwardOneToOneRelation)
    return {
        name: field
        for name, field in fields_map.items()
        if name not in generated_sources and not isinstance(field, reverse_types)
    }


EXPECTED_FIELDS = {
    collection.TopologyProfileRevision: BASE_FIELDS
    | {
        "topology_row",
        "revision",
        "resource_revision",
        "profile_json",
        "profile_hash",
        "created_by",
    },
    collection.TopologyCollectionManifest: BASE_FIELDS
    | {
        "topology_row",
        "profile_revision_row",
        "manifest_id",
        "manifest_hash",
        "collection_sequence",
        "base_graph_revision",
        "base_publication_revision",
        "profile_revision",
        "selection_provenance",
        "expected_resources_json",
        "capability_registry_version",
        "upgrade_intent_row",
        "issued_at",
        "expires_at",
        "consumed_at",
        "status",
        "creator_subject",
        "playbook_id",
        "run_id",
        "committed_snapshot_row",
        "committed_snapshot_id",
        "committed_payload_hash",
        "committed_analysis_run_row",
    },
    collection.TopologyCollectionSnapshot: BASE_FIELDS
    | {
        "topology_row",
        "target_manifest_row",
        "profile_revision_row",
        "payload_object_row",
        "redacted_payload",
        "snapshot_id",
        "schema_version",
        "collector_metadata",
        "collection_started_at",
        "collection_ended_at",
        "received_at",
        "manifest_id",
        "manifest_hash",
        "collection_sequence",
        "base_graph_revision",
        "base_publication_revision",
        "profile_revision",
        "payload_hash",
        "completeness_summary",
        "status",
        "retention_until",
        "creator_subject",
        "playbook_id",
        "run_id",
    },
    collection.TopologySnapshotUploadSession: BASE_FIELDS
    | {
        "topology_row",
        "manifest_row",
        "committed_snapshot_row",
        "upload_id",
        "snapshot_id",
        "envelope_json",
        "envelope_hash",
        "status",
        "client_expected_input_hash",
        "expected_observation_count",
        "authoritative_payload_hash",
        "submitter_subject",
        "playbook_id",
        "run_id",
        "expires_at",
        "committed_at",
        "committed_analysis_run_row",
    },
    collection.TopologyObservationStaging: BASE_FIELDS
    | {
        "topology_row",
        "upload_session_row",
        "observation_id",
        "resource_id",
        "content_hash",
        "redacted_sha256",
        "observation_payload",
        "size_bytes",
        "received_at",
    },
    collection.TopologyPayloadObject: BASE_FIELDS
    | {
        "content_hash",
        "object_key",
        "encryption_version",
        "key_version",
        "size_bytes",
        "status",
        "reference_count",
        "verified_at",
        "last_referenced_at",
        "retention_until",
    },
    analysis.TopologyAnalysisRun: BASE_FIELDS
    | {
        "topology_row",
        "snapshot_row",
        "target_profile_revision_row",
        "fact_graph_payload_object_row",
        "validation_report_object_row",
        "submitter_subject",
        "playbook_id",
        "run_id",
        "analysis_kind",
        "publication_attempt_id",
        "publish_as_current",
        "expected_publication_revision",
        "expected_current_graph_revision",
        "parser_bundle_version",
        "model_provider",
        "model_name",
        "prompt_version",
        "input_schema_version",
        "facts_schema_version",
        "graph_schema_version",
        "redaction_version",
        "deterministic_parser_versions",
        "fact_graph_payload",
        "validation_report",
        "facts_hash",
        "graph_hash",
        "state",
        "stage",
        "blocked_by",
        "error_code",
        "error_summary",
        "attempt_count",
        "max_attempts",
        "next_retry_at",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "lease_generation",
        "fencing_token",
        "started_at",
        "finished_at",
        "retryable",
        "cancellation_requested",
        "cancellation_requested_by",
        "cancellation_requested_at",
        "cancellation_reason",
    },
}
EXPECTED_FOREIGN_KEYS = {
    (collection.TopologyProfileRevision, "topology_row"): (
        "topology_row_id",
        fields.RESTRICT,
        False,
    ),
    (collection.TopologyCollectionManifest, "topology_row"): (
        "topology_row_id",
        fields.RESTRICT,
        False,
    ),
    (collection.TopologyCollectionManifest, "profile_revision_row"): (
        "profile_revision_row_id",
        fields.RESTRICT,
        False,
    ),
    (collection.TopologyCollectionManifest, "upgrade_intent_row"): (
        "upgrade_intent_row_id",
        fields.RESTRICT,
        True,
    ),
    (collection.TopologyCollectionManifest, "committed_snapshot_row"): (
        "committed_snapshot_row_id",
        fields.RESTRICT,
        True,
    ),
    (collection.TopologyCollectionManifest, "committed_analysis_run_row"): (
        "committed_analysis_run_row_id",
        fields.RESTRICT,
        True,
    ),
    (collection.TopologyCollectionSnapshot, "target_manifest_row"): (
        "target_manifest_row_id",
        fields.RESTRICT,
        False,
    ),
    (collection.TopologyCollectionSnapshot, "topology_row"): (
        "topology_row_id",
        fields.RESTRICT,
        False,
    ),
    (collection.TopologyCollectionSnapshot, "profile_revision_row"): (
        "profile_revision_row_id",
        fields.RESTRICT,
        False,
    ),
    (collection.TopologyCollectionSnapshot, "payload_object_row"): (
        "payload_object_row_id",
        fields.RESTRICT,
        True,
    ),
    (collection.TopologySnapshotUploadSession, "manifest_row"): (
        "manifest_row_id",
        fields.RESTRICT,
        False,
    ),
    (collection.TopologySnapshotUploadSession, "topology_row"): (
        "topology_row_id",
        fields.RESTRICT,
        False,
    ),
    (collection.TopologySnapshotUploadSession, "committed_snapshot_row"): (
        "committed_snapshot_row_id",
        fields.RESTRICT,
        True,
    ),
    (collection.TopologySnapshotUploadSession, "committed_analysis_run_row"): (
        "committed_analysis_run_row_id",
        fields.RESTRICT,
        True,
    ),
    (collection.TopologyObservationStaging, "upload_session_row"): (
        "upload_session_row_id",
        fields.CASCADE,
        False,
    ),
    (collection.TopologyObservationStaging, "topology_row"): (
        "topology_row_id",
        fields.RESTRICT,
        False,
    ),
    (analysis.TopologyAnalysisRun, "topology_row"): (
        "topology_row_id",
        fields.RESTRICT,
        False,
    ),
    (analysis.TopologyAnalysisRun, "snapshot_row"): (
        "snapshot_row_id",
        fields.RESTRICT,
        False,
    ),
    (analysis.TopologyAnalysisRun, "target_profile_revision_row"): (
        "target_profile_revision_row_id",
        fields.RESTRICT,
        False,
    ),
    (analysis.TopologyAnalysisRun, "fact_graph_payload_object_row"): (
        "fact_graph_payload_object_row_id",
        fields.RESTRICT,
        True,
    ),
    (analysis.TopologyAnalysisRun, "validation_report_object_row"): (
        "validation_report_object_row_id",
        fields.RESTRICT,
        True,
    ),
}
EXPECTED_NULLABLE = {
    collection.TopologyProfileRevision: set(),
    collection.TopologyCollectionManifest: {
        "upgrade_intent_row",
        "consumed_at",
        "committed_snapshot_row",
        "committed_snapshot_id",
        "committed_payload_hash",
        "committed_analysis_run_row",
    },
    collection.TopologyCollectionSnapshot: {
        "payload_object_row",
        "redacted_payload",
    },
    collection.TopologySnapshotUploadSession: {
        "committed_snapshot_row",
        "client_expected_input_hash",
        "authoritative_payload_hash",
        "committed_at",
        "committed_analysis_run_row",
    },
    collection.TopologyObservationStaging: set(),
    collection.TopologyPayloadObject: {
        "verified_at",
        "last_referenced_at",
        "retention_until",
    },
    analysis.TopologyAnalysisRun: {
        "fact_graph_payload_object_row",
        "validation_report_object_row",
        "model_provider",
        "model_name",
        "prompt_version",
        "fact_graph_payload",
        "validation_report",
        "facts_hash",
        "graph_hash",
        "blocked_by",
        "error_code",
        "error_summary",
        "next_retry_at",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "started_at",
        "finished_at",
        "cancellation_requested_by",
        "cancellation_requested_at",
        "cancellation_reason",
    },
}
EXPECTED_INDEXES = {
    collection.TopologyProfileRevision: (
        ("tenant_id", "topology_row_id", "revision"),
        ("tenant_id", "topology_row_id", "resource_revision"),
    ),
    collection.TopologyCollectionManifest: (
        ("tenant_id", "topology_row_id", "manifest_id"),
        ("tenant_id", "topology_row_id", "collection_sequence"),
        ("tenant_id", "topology_row_id", "status"),
        ("tenant_id", "expires_at"),
    ),
    collection.TopologyCollectionSnapshot: (
        ("tenant_id", "topology_row_id", "snapshot_id"),
        ("tenant_id", "topology_row_id", "collection_sequence"),
        ("tenant_id", "received_at"),
        ("tenant_id", "retention_until"),
    ),
    collection.TopologySnapshotUploadSession: (
        ("tenant_id", "topology_row_id", "upload_id"),
        ("tenant_id", "topology_row_id", "status"),
        ("status", "expires_at"),
    ),
    collection.TopologyObservationStaging: (
        ("tenant_id", "upload_session_row_id", "observation_id"),
        ("tenant_id", "topology_row_id", "resource_id"),
    ),
    collection.TopologyPayloadObject: (
        ("tenant_id", "content_hash"),
        ("tenant_id", "status"),
        ("tenant_id", "retention_until"),
    ),
    analysis.TopologyAnalysisRun: (
        ("state", "next_retry_at", "lease_expires_at"),
        ("tenant_id", "topology_row_id", "state"),
        ("tenant_id", "snapshot_row_id", "ctime"),
        ("tenant_id", "topology_row_id", "publication_attempt_id"),
    ),
}


def test_modules_export_only_the_frozen_batch_surface() -> None:
    assert base.__all__ == (
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
    assert collection.__all__ == (
        "TopologyCollectionManifest",
        "TopologyCollectionSnapshot",
        "TopologyObservationStaging",
        "TopologyPayloadObject",
        "TopologyProfileRevision",
        "TopologySnapshotUploadSession",
    )
    assert analysis.__all__ == ("TopologyAnalysisRun",)


def test_public_facade_exports_complete_model_surface() -> None:
    assert public_v2.__all__ == (
        "PresentationOverride",
        "TopologyAnalysisRun",
        "TopologyCollectionManifest",
        "TopologyCollectionSnapshot",
        "TopologyIdentityAlias",
        "TopologyIdentityConflictAudit",
        "TopologyIdentityRegistry",
        "TopologyLayoutSet",
        "TopologyObservationStaging",
        "TopologyPayloadObject",
        "TopologyProfileRevision",
        "TopologyScopeLayoutVersion",
        "TopologySnapshotUploadSession",
        "TopologySyncAlert",
        "TopologyUpgradeIntent",
    )


def test_tenant_scope_base_is_explicit() -> None:
    tenant_fields = base.TopologyTenantModel._meta.fields_map
    assert base.TopologyTenantModel._meta.abstract is True
    assert tenant_fields["tenant_id"].null is False
    assert tenant_fields["tenant_id"].default is None
    assert "installation_id" not in tenant_fields

def test_frozen_state_and_stage_sets_are_exact() -> None:
    assert base.MANIFEST_STATUSES == {
        "ISSUED",
        "COMMITTED",
        "EXPIRED",
        "CANCELLED",
    }
    assert base.UPLOAD_SESSION_STATUSES == {
        "OPEN",
        "COMMITTING",
        "COMMITTED",
        "ABORTED",
        "EXPIRED",
    }
    assert base.PAYLOAD_OBJECT_STATUSES == {"STAGING", "READY", "LIVE", "DELETING"}
    assert base.ANALYSIS_RUN_STATES == {
        "RECEIVED",
        "RUNNING",
        "RETRY_WAIT",
        "WAITING_FOR_PUBLISH",
        "SUCCEEDED",
        "FAILED",
        "SUPERSEDED",
        "CANCELLED",
    }
    assert base.ANALYSIS_KINDS == {"SNAPSHOT", "REANALYZE"}
    assert base.ANALYSIS_RUN_STAGES == {
        "SPLITTING",
        "PARSING",
        "VALIDATING_FACTS",
        "FACTS_READY",
        "RESOLVING_IDENTITIES",
        "BUILDING_SCOPES",
        "PROJECTING_GRAPH",
        "VALIDATING_GRAPH",
        "PUBLISHING",
    }
    run_fields = analysis.TopologyAnalysisRun._meta.fields_map
    assert run_fields["state"].default == "RECEIVED"
    assert run_fields["stage"].default == "SPLITTING"
    assert run_fields["publish_as_current"].default is False
    assert run_fields["state"].default in base.ANALYSIS_RUN_STATES
    assert run_fields["stage"].default in base.ANALYSIS_RUN_STAGES
    assert "next_attempt_at" not in run_fields


def test_batch_models_freeze_tables_and_exact_declared_fields() -> None:
    expected_tables = {
        collection.TopologyProfileRevision: "network_topology_profile_revision",
        collection.TopologyCollectionManifest: "network_topology_collection_manifest",
        collection.TopologyCollectionSnapshot: "network_topology_collection_snapshot",
        collection.TopologySnapshotUploadSession: "network_topology_snapshot_upload_session",
        collection.TopologyObservationStaging: "network_topology_observation_staging",
        collection.TopologyPayloadObject: "network_topology_payload_object",
        analysis.TopologyAnalysisRun: "network_topology_analysis_run",
    }
    for model, expected_fields in EXPECTED_FIELDS.items():
        assert issubclass(model, base.TopologyTenantModel)
        assert set(_declared_fields(model)) == expected_fields
        assert model._meta.db_table == expected_tables[model]
        assert model._meta.abstract is False


def test_batch_models_freeze_exact_nullability() -> None:
    for model, expected in EXPECTED_NULLABLE.items():
        actual = {name for name, field in _declared_fields(model).items() if field.null}
        assert actual == expected


def test_snapshot_payload_modes_defer_exactly_one_constraint() -> None:
    snapshot_fields = collection.TopologyCollectionSnapshot._meta.fields_map
    assert snapshot_fields["redacted_payload"].null is True
    assert snapshot_fields["payload_object_row"].null is True
    assert "exactly one payload mode" in collection.TopologyCollectionSnapshot.__doc__
    assert "Migration C" in collection.TopologyCollectionSnapshot.__doc__


def test_foreign_keys_freeze_source_columns_and_delete_policy() -> None:
    for (model, field_name), contract in EXPECTED_FOREIGN_KEYS.items():
        source_field, on_delete, nullable = contract
        field = model._meta.fields_map[field_name]
        assert isinstance(field, ForeignKeyFieldInstance)
        assert field.source_field == source_field
        assert field.on_delete == on_delete
        assert field.null is nullable


def test_sensitive_payloads_are_encrypted_or_object_referenced() -> None:
    encrypted_fields = {
        collection.TopologyProfileRevision: {"profile_json"},
        collection.TopologyCollectionManifest: {
            "selection_provenance",
            "expected_resources_json",
        },
        collection.TopologyCollectionSnapshot: {
            "collector_metadata",
            "completeness_summary",
            "redacted_payload",
        },
        collection.TopologySnapshotUploadSession: {"envelope_json"},
        collection.TopologyObservationStaging: {"observation_payload"},
        collection.TopologyPayloadObject: {"object_key"},
        analysis.TopologyAnalysisRun: {
            "deterministic_parser_versions",
            "fact_graph_payload",
            "validation_report",
            "error_summary",
            "cancellation_reason",
        },
    }
    for model, field_names in encrypted_fields.items():
        for field_name in field_names:
            assert isinstance(model._meta.fields_map[field_name], EncryptedTextField)
        assert not any(
            isinstance(field, fields.JSONField)
            for field in model._meta.fields_map.values()
        )
        assert {"private_key", "secret"}.isdisjoint(model._meta.fields_map)


def test_revisions_sequences_and_sizes_use_bigints() -> None:
    expected = {
        collection.TopologyProfileRevision: {"revision", "resource_revision"},
        collection.TopologyCollectionManifest: {
            "collection_sequence",
            "base_graph_revision",
            "base_publication_revision",
            "profile_revision",
        },
        collection.TopologyCollectionSnapshot: {
            "collection_sequence",
            "base_graph_revision",
            "base_publication_revision",
            "profile_revision",
        },
        collection.TopologySnapshotUploadSession: {"expected_observation_count"},
        collection.TopologyObservationStaging: {"size_bytes"},
        collection.TopologyPayloadObject: {"size_bytes", "reference_count"},
        analysis.TopologyAnalysisRun: {
            "expected_publication_revision",
            "expected_current_graph_revision",
            "attempt_count",
            "max_attempts",
            "lease_generation",
            "fencing_token",
        },
    }
    for model, field_names in expected.items():
        for field_name in field_names:
            assert isinstance(model._meta.fields_map[field_name], fields.BigIntField)


def test_remaining_models_defer_unrelated_constraints_but_keep_query_indexes() -> None:
    for model in set(EXPECTED_FIELDS) - {collection.TopologyCollectionSnapshot}:
        assert model._meta.unique_together == ()
        assert all(
            not field.unique
            for name, field in _declared_fields(model).items()
            if name != "id"
        )
    for model, expected in EXPECTED_INDEXES.items():
        assert model._meta.indexes == expected
    target_manifest = collection.TopologyCollectionSnapshot._meta.fields_map[
        "target_manifest_row"
    ]
    assert target_manifest.null is False
    assert target_manifest.unique is True


def test_manifest_committed_binding_contract_is_explicit() -> None:
    manifest_fields = collection.TopologyCollectionManifest._meta.fields_map
    committed_rows = {
        "committed_snapshot_row": "committed_snapshot_row_id",
        "committed_analysis_run_row": "committed_analysis_run_row_id",
    }
    for field_name, source_field in committed_rows.items():
        field = manifest_fields[field_name]
        assert isinstance(field, ForeignKeyFieldInstance)
        assert field.source_field == source_field
        assert field.null is True
        assert field.on_delete == fields.RESTRICT
    payload_hash = manifest_fields["committed_payload_hash"]
    assert payload_hash.null is True
    assert payload_hash.max_length == 71


def test_indexes_cover_task_1_1_query_paths() -> None:
    expected = {
        collection.TopologyCollectionManifest: (
            "tenant_id", "topology_row_id", "collection_sequence"
        ),
        analysis.TopologyAnalysisRun: (
            "state", "next_retry_at", "lease_expires_at"
        ),
        collection.TopologySnapshotUploadSession: ("status", "expires_at"),
        identity.TopologyIdentityAlias: (
            "tenant_id", "topology_row_id", "alias_type",
            "alias_namespace", "alias_value_hash",
        ),
        collection.TopologyProfileRevision: (
            "tenant_id", "topology_row_id", "revision"
        ),
        NetworkTopologyVersion: ("tenant_id", "topology_id", "version"),
    }
    for model, query_index in expected.items():
        assert query_index in model._meta.indexes


def test_topology_scoped_v2_models_have_tenant_topology_index_prefix() -> None:
    expected_prefix = ("tenant_id", "topology_row_id")
    for model_name in public_v2.__all__:
        model = getattr(public_v2, model_name)
        if "topology_row_id" not in model._meta.fields_map:
            continue
        assert any(index[:2] == expected_prefix for index in model._meta.indexes)


def test_production_tortoise_discovery_resolves_public_v2_surface() -> None:
    probe = """
from tortoise import Tortoise
Tortoise.init_models(['models'], 'models')
import models
from models import (
    NetworkTopology, NetworkTopologyVersion, TopologyAnalysisRun,
    TopologyUpgradeIntent,
)
from models.network_topology_v2 import __all__ as public_names
from models import (
    TopologyCollectionManifest,
    TopologyCollectionSnapshot,
    TopologyObservationStaging,
    TopologySnapshotUploadSession,
)
assert all(getattr(models, name)._meta.app == 'models' for name in public_names)
assert TopologyCollectionSnapshot._meta.fields_map['target_manifest_row'].related_model is TopologyCollectionManifest
assert TopologyCollectionManifest._meta.fields_map['committed_snapshot_row'].related_model is TopologyCollectionSnapshot
assert TopologyCollectionManifest._meta.fields_map['committed_analysis_run_row'].related_model is TopologyAnalysisRun
assert TopologyCollectionManifest._meta.fields_map['upgrade_intent_row'].related_model is TopologyUpgradeIntent
assert TopologySnapshotUploadSession._meta.fields_map['committed_analysis_run_row'].related_model is TopologyAnalysisRun
assert TopologyAnalysisRun._meta.fields_map['snapshot_row'].related_model is TopologyCollectionSnapshot
assert TopologyObservationStaging._meta.fields_map['upload_session_row'].related_model.__name__ == 'TopologySnapshotUploadSession'
assert NetworkTopology._meta.fields_map['current_analysis_run_row'].related_model is TopologyAnalysisRun
assert NetworkTopologyVersion._meta.fields_map['analysis_run_row'].related_model is TopologyAnalysisRun
print('production-discovery-and-fk-resolution-ok')
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "production-discovery-and-fk-resolution-ok"
