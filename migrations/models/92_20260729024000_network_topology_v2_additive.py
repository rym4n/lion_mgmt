"""Phase C.3 additive topology-v2 schema generated from the finalized ORM models.

This migration intentionally creates only topology business tables.  Existing
legacy tables receive nullable columns and additive indexes; no legacy column,
constraint, or index is removed or tightened in this phase.
"""

from __future__ import annotations

from hashlib import sha1

from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


_BASE_COLUMNS = (
    '"id" VARCHAR(36) NOT NULL PRIMARY KEY',
    '"ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP',
    '"utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP',
    '"deleted" BOOL NOT NULL DEFAULT FALSE',
    '"tenant_id" VARCHAR(255) NOT NULL',
)


# fmt: off
_TABLE_COLUMNS = (
    ("network_topology_payload_object", (
        '"content_hash" VARCHAR(71) NOT NULL',
        '"object_key" TEXT NOT NULL',
        '"encryption_version" VARCHAR(64) NOT NULL',
        '"key_version" VARCHAR(128) NOT NULL',
        '"size_bytes" BIGINT NOT NULL',
        '"status" VARCHAR(8) NOT NULL DEFAULT \'STAGING\'',
        '"reference_count" BIGINT NOT NULL DEFAULT 0',
        '"verified_at" TIMESTAMPTZ',
        '"last_referenced_at" TIMESTAMPTZ',
        '"retention_until" TIMESTAMPTZ',
    )),
    ("network_topology_profile_revision", (
        '"revision" BIGINT NOT NULL',
        '"resource_revision" BIGINT NOT NULL',
        '"profile_json" TEXT NOT NULL',
        '"profile_hash" VARCHAR(71) NOT NULL',
        '"created_by" VARCHAR(255) NOT NULL',
        '"topology_row_id" VARCHAR(36) NOT NULL',
    )),
    ("network_topology_upgrade_intent", (
        '"expected_legacy_revision" BIGINT NOT NULL',
        '"status" VARCHAR(9) NOT NULL DEFAULT \'ACTIVE\'',
        '"created_by" VARCHAR(255) NOT NULL',
        '"expires_at" TIMESTAMPTZ NOT NULL',
        '"consumed_at" TIMESTAMPTZ',
        '"consumed_by" VARCHAR(255)',
        '"cancelled_at" TIMESTAMPTZ',
        '"cancelled_by" VARCHAR(255)',
        '"cancellation_reason" TEXT',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"legacy_graph_version_row_id" VARCHAR(36) NOT NULL',
    )),
    ("network_topology_scope_layout_version", (
        '"scope_id" VARCHAR(255) NOT NULL',
        '"scope_layout_revision" BIGINT NOT NULL',
        '"graph_revision" BIGINT NOT NULL',
        '"layout_json" TEXT NOT NULL',
        '"viewport_json" TEXT NOT NULL',
        '"created_by" VARCHAR(255) NOT NULL',
        '"topology_row_id" VARCHAR(36) NOT NULL',
    )),
    ("network_topology_layout_set", (
        '"layout_set_revision" BIGINT NOT NULL',
        '"scope_version_map_json" TEXT NOT NULL',
        '"created_by" VARCHAR(255) NOT NULL',
        '"topology_row_id" VARCHAR(36) NOT NULL',
    )),
    ("network_topology_presentation_override", (
        '"target_stable_id" VARCHAR(255) NOT NULL',
        '"override_kind" VARCHAR(64) NOT NULL',
        '"override_value" TEXT NOT NULL',
        '"status" VARCHAR(32) NOT NULL DEFAULT \'ACTIVE\'',
        '"created_by" VARCHAR(255) NOT NULL',
        '"orphaned_at" TIMESTAMPTZ',
        '"migrated_to_target_stable_id" VARCHAR(255)',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"profile_revision_row_id" VARCHAR(36) NOT NULL',
    )),
    ("network_topology_collection_manifest", (
        '"manifest_id" VARCHAR(255) NOT NULL',
        '"manifest_hash" VARCHAR(71) NOT NULL',
        '"collection_sequence" BIGINT NOT NULL',
        '"base_graph_revision" BIGINT NOT NULL',
        '"base_publication_revision" BIGINT NOT NULL',
        '"profile_revision" BIGINT NOT NULL',
        '"selection_provenance" TEXT NOT NULL',
        '"expected_resources_json" TEXT NOT NULL',
        '"capability_registry_version" VARCHAR(255) NOT NULL',
        '"issued_at" TIMESTAMPTZ NOT NULL',
        '"expires_at" TIMESTAMPTZ NOT NULL',
        '"consumed_at" TIMESTAMPTZ',
        '"status" VARCHAR(9) NOT NULL DEFAULT \'ISSUED\'',
        '"creator_subject" VARCHAR(255) NOT NULL',
        '"playbook_id" VARCHAR(255) NOT NULL',
        '"run_id" VARCHAR(255) NOT NULL',
        '"committed_snapshot_id" VARCHAR(255)',
        '"committed_payload_hash" VARCHAR(71)',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"profile_revision_row_id" VARCHAR(36) NOT NULL',
        '"upgrade_intent_row_id" VARCHAR(36)',
        '"committed_snapshot_row_id" VARCHAR(36)',
        '"committed_analysis_run_row_id" VARCHAR(36)',
    )),
    ("network_topology_collection_snapshot", (
        '"redacted_payload" TEXT',
        '"snapshot_id" VARCHAR(255) NOT NULL',
        '"schema_version" VARCHAR(255) NOT NULL',
        '"collector_metadata" TEXT NOT NULL',
        '"collection_started_at" TIMESTAMPTZ NOT NULL',
        '"collection_ended_at" TIMESTAMPTZ NOT NULL',
        '"received_at" TIMESTAMPTZ NOT NULL',
        '"manifest_id" VARCHAR(255) NOT NULL',
        '"manifest_hash" VARCHAR(71) NOT NULL',
        '"collection_sequence" BIGINT NOT NULL',
        '"base_graph_revision" BIGINT NOT NULL',
        '"base_publication_revision" BIGINT NOT NULL',
        '"profile_revision" BIGINT NOT NULL',
        '"payload_hash" VARCHAR(71) NOT NULL',
        '"completeness_summary" TEXT NOT NULL',
        '"status" VARCHAR(32) NOT NULL',
        '"retention_until" TIMESTAMPTZ NOT NULL',
        '"creator_subject" VARCHAR(255) NOT NULL',
        '"playbook_id" VARCHAR(255) NOT NULL',
        '"run_id" VARCHAR(255) NOT NULL',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"target_manifest_row_id" VARCHAR(36) NOT NULL',
        '"profile_revision_row_id" VARCHAR(36) NOT NULL',
        '"payload_object_row_id" VARCHAR(36)',
    )),
    ("network_topology_analysis_run", (
        '"submitter_subject" VARCHAR(255) NOT NULL',
        '"playbook_id" VARCHAR(255) NOT NULL',
        '"run_id" VARCHAR(255) NOT NULL',
        '"analysis_kind" VARCHAR(9) NOT NULL',
        '"publication_attempt_id" VARCHAR(255) NOT NULL',
        '"publish_as_current" BOOL NOT NULL DEFAULT FALSE',
        '"expected_publication_revision" BIGINT NOT NULL',
        '"expected_current_graph_revision" BIGINT NOT NULL',
        '"parser_bundle_version" VARCHAR(255) NOT NULL',
        '"model_provider" VARCHAR(255)',
        '"model_name" VARCHAR(255)',
        '"prompt_version" VARCHAR(255)',
        '"input_schema_version" VARCHAR(255) NOT NULL',
        '"facts_schema_version" VARCHAR(255) NOT NULL',
        '"graph_schema_version" VARCHAR(255) NOT NULL',
        '"redaction_version" VARCHAR(255) NOT NULL',
        '"deterministic_parser_versions" TEXT NOT NULL',
        '"fact_graph_payload" TEXT',
        '"validation_report" TEXT',
        '"facts_hash" VARCHAR(71)',
        '"graph_hash" VARCHAR(71)',
        '"state" VARCHAR(19) NOT NULL DEFAULT \'RECEIVED\'',
        '"stage" VARCHAR(20) NOT NULL DEFAULT \'SPLITTING\'',
        '"blocked_by" VARCHAR(64)',
        '"error_code" VARCHAR(128)',
        '"error_summary" TEXT',
        '"attempt_count" BIGINT NOT NULL DEFAULT 0',
        '"max_attempts" BIGINT NOT NULL DEFAULT 3',
        '"next_retry_at" TIMESTAMPTZ',
        '"lease_owner" VARCHAR(255)',
        '"lease_expires_at" TIMESTAMPTZ',
        '"heartbeat_at" TIMESTAMPTZ',
        '"lease_generation" BIGINT NOT NULL DEFAULT 0',
        '"fencing_token" BIGINT NOT NULL DEFAULT 0',
        '"started_at" TIMESTAMPTZ',
        '"finished_at" TIMESTAMPTZ',
        '"retryable" BOOL NOT NULL DEFAULT TRUE',
        '"cancellation_requested" BOOL NOT NULL DEFAULT FALSE',
        '"cancellation_requested_by" VARCHAR(255)',
        '"cancellation_requested_at" TIMESTAMPTZ',
        '"cancellation_reason" TEXT',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"snapshot_row_id" VARCHAR(36) NOT NULL',
        '"target_profile_revision_row_id" VARCHAR(36) NOT NULL',
        '"fact_graph_payload_object_row_id" VARCHAR(36)',
        '"validation_report_object_row_id" VARCHAR(36)',
    )),
    ("network_topology_snapshot_upload_session", (
        '"upload_id" VARCHAR(255) NOT NULL',
        '"snapshot_id" VARCHAR(255) NOT NULL',
        '"envelope_json" TEXT NOT NULL',
        '"envelope_hash" VARCHAR(71) NOT NULL',
        '"status" VARCHAR(10) NOT NULL DEFAULT \'OPEN\'',
        '"client_expected_input_hash" VARCHAR(71)',
        '"expected_observation_count" BIGINT NOT NULL',
        '"authoritative_payload_hash" VARCHAR(71)',
        '"submitter_subject" VARCHAR(255) NOT NULL',
        '"playbook_id" VARCHAR(255) NOT NULL',
        '"run_id" VARCHAR(255) NOT NULL',
        '"expires_at" TIMESTAMPTZ NOT NULL',
        '"committed_at" TIMESTAMPTZ',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"manifest_row_id" VARCHAR(36) NOT NULL',
        '"committed_snapshot_row_id" VARCHAR(36)',
        '"committed_analysis_run_row_id" VARCHAR(36)',
    )),
    ("network_topology_observation_staging", (
        '"observation_id" VARCHAR(255) NOT NULL',
        '"resource_id" VARCHAR(255) NOT NULL',
        '"content_hash" VARCHAR(71) NOT NULL',
        '"redacted_sha256" VARCHAR(71) NOT NULL',
        '"observation_payload" TEXT NOT NULL',
        '"size_bytes" BIGINT NOT NULL',
        '"received_at" TIMESTAMPTZ NOT NULL',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"upload_session_row_id" VARCHAR(36) NOT NULL',
    )),
    ("network_topology_identity_registry", (
        '"canonical_id" VARCHAR(255) NOT NULL',
        '"entity_kind" VARCHAR(64) NOT NULL',
        '"canonical_fingerprint" VARCHAR(71) NOT NULL',
        '"status" VARCHAR(32) NOT NULL DEFAULT \'ACTIVE\'',
        '"created_by" VARCHAR(255) NOT NULL',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"first_seen_analysis_run_row_id" VARCHAR(36) NOT NULL',
        '"last_seen_analysis_run_row_id" VARCHAR(36) NOT NULL',
    )),
    ("network_topology_identity_alias", (
        '"alias_type" VARCHAR(64) NOT NULL',
        '"alias_namespace" VARCHAR(255) NOT NULL',
        '"alias_value" TEXT NOT NULL',
        '"alias_value_hash" VARCHAR(71) NOT NULL',
        '"status" VARCHAR(32) NOT NULL DEFAULT \'ACTIVE\'',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"identity_row_id" VARCHAR(36) NOT NULL',
        '"first_seen_analysis_run_row_id" VARCHAR(36) NOT NULL',
        '"last_seen_analysis_run_row_id" VARCHAR(36) NOT NULL',
    )),
    ("network_topology_identity_conflict_audit", (
        '"alias_type" VARCHAR(64) NOT NULL',
        '"alias_namespace" VARCHAR(255) NOT NULL',
        '"alias_value_hash" VARCHAR(71) NOT NULL',
        '"status" VARCHAR(32) NOT NULL DEFAULT \'UNRESOLVED\'',
        '"conflict_details" TEXT NOT NULL',
        '"resolved_by" VARCHAR(255)',
        '"resolved_at" TIMESTAMPTZ',
        '"resolution" TEXT',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"analysis_run_row_id" VARCHAR(36) NOT NULL',
        '"incumbent_identity_row_id" VARCHAR(36) NOT NULL',
        '"candidate_identity_row_id" VARCHAR(36)',
    )),
    ("network_topology_sync_alert", (
        '"sync_id" VARCHAR(255) NOT NULL',
        '"alert_type" VARCHAR(128) NOT NULL',
        '"severity" VARCHAR(32) NOT NULL',
        '"status" VARCHAR(32) NOT NULL DEFAULT \'OPEN\'',
        '"owner_subject" VARCHAR(255)',
        '"details" TEXT NOT NULL',
        '"opened_at" TIMESTAMPTZ NOT NULL',
        '"closed_at" TIMESTAMPTZ',
        '"closed_by" VARCHAR(255)',
        '"topology_row_id" VARCHAR(36) NOT NULL',
        '"analysis_run_row_id" VARCHAR(36)',
    )),
)


_LEGACY_DDL = """ALTER TABLE "network_topology"
    ADD COLUMN IF NOT EXISTS "document_schema_version" VARCHAR(255),
    ADD COLUMN IF NOT EXISTS "generation_source" VARCHAR(64),
    ADD COLUMN IF NOT EXISTS "root_scope_id" VARCHAR(255),
    ADD COLUMN IF NOT EXISTS "topology_profile_revision" BIGINT,
    ADD COLUMN IF NOT EXISTS "resource_revision" BIGINT,
    ADD COLUMN IF NOT EXISTS "current_graph_revision" BIGINT,
    ADD COLUMN IF NOT EXISTS "publication_revision" BIGINT,
    ADD COLUMN IF NOT EXISTS "current_layout_set_revision" BIGINT,
    ADD COLUMN IF NOT EXISTS "fact_hash" VARCHAR(71),
    ADD COLUMN IF NOT EXISTS "graph_hash" VARCHAR(71),
    ADD COLUMN IF NOT EXISTS "last_published_collection_sequence" BIGINT,
    ADD COLUMN IF NOT EXISTS "legacy_write_generation" BIGINT,
    ADD COLUMN IF NOT EXISTS "current_graph_version_row_id" VARCHAR(36),
    ADD COLUMN IF NOT EXISTS "current_snapshot_row_id" VARCHAR(36),
    ADD COLUMN IF NOT EXISTS "current_analysis_run_row_id" VARCHAR(36),
    ADD COLUMN IF NOT EXISTS "current_layout_set_row_id" VARCHAR(36);

ALTER TABLE "network_topology_version"
    ADD COLUMN IF NOT EXISTS "document_schema_version" VARCHAR(255),
    ADD COLUMN IF NOT EXISTS "presentation_graph_payload" TEXT,
    ADD COLUMN IF NOT EXISTS "presentation_graph_payload_object_row_id" VARCHAR(36),
    ADD COLUMN IF NOT EXISTS "snapshot_row_id" VARCHAR(36),
    ADD COLUMN IF NOT EXISTS "analysis_run_row_id" VARCHAR(36),
    ADD COLUMN IF NOT EXISTS "profile_revision_row_id" VARCHAR(36),
    ADD COLUMN IF NOT EXISTS "facts_hash" VARCHAR(71),
    ADD COLUMN IF NOT EXISTS "graph_hash" VARCHAR(71),
    ADD COLUMN IF NOT EXISTS "published_collection_sequence" BIGINT,
    ADD COLUMN IF NOT EXISTS "publication_revision" BIGINT,
    ADD COLUMN IF NOT EXISTS "published_layout_set_row_id" VARCHAR(36);"""


_INDEX_SPECS = (
    ("network_topology_payload_object", ("tenant_id", "content_hash"), False),
    ("network_topology_payload_object", ("tenant_id", "status"), False),
    ("network_topology_payload_object", ("tenant_id", "retention_until"), False),
    ("network_topology_profile_revision", ("tenant_id", "topology_row_id", "revision"), False),
    ("network_topology_profile_revision", ("tenant_id", "topology_row_id", "resource_revision"), False),
    ("network_topology_upgrade_intent", ("tenant_id", "topology_row_id", "status"), False),
    ("network_topology_upgrade_intent", ("tenant_id", "expires_at"), False),
    ("network_topology_scope_layout_version", ("tenant_id", "topology_row_id", "scope_id", "scope_layout_revision"), False),
    ("network_topology_scope_layout_version", ("tenant_id", "topology_row_id", "graph_revision"), False),
    ("network_topology_layout_set", ("tenant_id", "topology_row_id", "layout_set_revision"), False),
    ("network_topology_presentation_override", ("tenant_id", "topology_row_id", "target_stable_id", "status"), False),
    ("network_topology_presentation_override", ("tenant_id", "topology_row_id", "profile_revision_row_id"), False),
    ("network_topology_collection_manifest", ("tenant_id", "topology_row_id", "manifest_id"), False),
    ("network_topology_collection_manifest", ("tenant_id", "topology_row_id", "collection_sequence"), False),
    ("network_topology_collection_manifest", ("tenant_id", "topology_row_id", "status"), False),
    ("network_topology_collection_manifest", ("tenant_id", "expires_at"), False),
    ("network_topology_collection_snapshot", ("tenant_id", "topology_row_id", "snapshot_id"), False),
    ("network_topology_collection_snapshot", ("tenant_id", "topology_row_id", "collection_sequence"), False),
    ("network_topology_collection_snapshot", ("tenant_id", "received_at"), False),
    ("network_topology_collection_snapshot", ("tenant_id", "retention_until"), False),
    ("network_topology_collection_snapshot", ("target_manifest_row_id",), True),
    ("network_topology_analysis_run", ("state", "next_retry_at", "lease_expires_at"), False),
    ("network_topology_analysis_run", ("tenant_id", "topology_row_id", "state"), False),
    ("network_topology_analysis_run", ("tenant_id", "snapshot_row_id", "ctime"), False),
    ("network_topology_analysis_run", ("tenant_id", "topology_row_id", "publication_attempt_id"), False),
    ("network_topology_snapshot_upload_session", ("tenant_id", "topology_row_id", "upload_id"), False),
    ("network_topology_snapshot_upload_session", ("tenant_id", "topology_row_id", "status"), False),
    ("network_topology_snapshot_upload_session", ("status", "expires_at"), False),
    ("network_topology_observation_staging", ("tenant_id", "upload_session_row_id", "observation_id"), False),
    ("network_topology_observation_staging", ("tenant_id", "topology_row_id", "resource_id"), False),
    ("network_topology_identity_registry", ("tenant_id", "topology_row_id", "canonical_id"), False),
    ("network_topology_identity_registry", ("tenant_id", "topology_row_id", "entity_kind", "status"), False),
    ("network_topology_identity_alias", ("tenant_id", "topology_row_id", "alias_type", "alias_namespace", "alias_value_hash"), False),
    ("network_topology_identity_alias", ("tenant_id", "identity_row_id", "status"), False),
    ("network_topology_identity_conflict_audit", ("tenant_id", "topology_row_id", "status"), False),
    ("network_topology_identity_conflict_audit", ("tenant_id", "topology_row_id", "alias_type", "alias_namespace", "alias_value_hash"), False),
    ("network_topology_sync_alert", ("tenant_id", "sync_id", "alert_type"), False),
    ("network_topology_sync_alert", ("tenant_id", "topology_row_id", "status"), False),
    ("network_topology_version", ("tenant_id", "topology_id", "version"), False),
)


_FK_SPECS = (
    ("network_topology", "current_graph_version_row_id", "network_topology_version", "RESTRICT"),
    ("network_topology", "current_snapshot_row_id", "network_topology_collection_snapshot", "RESTRICT"),
    ("network_topology", "current_analysis_run_row_id", "network_topology_analysis_run", "RESTRICT"),
    ("network_topology", "current_layout_set_row_id", "network_topology_layout_set", "RESTRICT"),
    ("network_topology_version", "presentation_graph_payload_object_row_id", "network_topology_payload_object", "RESTRICT"),
    ("network_topology_version", "snapshot_row_id", "network_topology_collection_snapshot", "RESTRICT"),
    ("network_topology_version", "analysis_run_row_id", "network_topology_analysis_run", "RESTRICT"),
    ("network_topology_version", "profile_revision_row_id", "network_topology_profile_revision", "RESTRICT"),
    ("network_topology_version", "published_layout_set_row_id", "network_topology_layout_set", "RESTRICT"),
    ("network_topology_profile_revision", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_upgrade_intent", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_upgrade_intent", "legacy_graph_version_row_id", "network_topology_version", "RESTRICT"),
    ("network_topology_scope_layout_version", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_layout_set", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_presentation_override", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_presentation_override", "profile_revision_row_id", "network_topology_profile_revision", "RESTRICT"),
    ("network_topology_collection_manifest", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_collection_manifest", "profile_revision_row_id", "network_topology_profile_revision", "RESTRICT"),
    ("network_topology_collection_manifest", "upgrade_intent_row_id", "network_topology_upgrade_intent", "RESTRICT"),
    ("network_topology_collection_manifest", "committed_snapshot_row_id", "network_topology_collection_snapshot", "RESTRICT"),
    ("network_topology_collection_manifest", "committed_analysis_run_row_id", "network_topology_analysis_run", "RESTRICT"),
    ("network_topology_collection_snapshot", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_collection_snapshot", "target_manifest_row_id", "network_topology_collection_manifest", "RESTRICT"),
    ("network_topology_collection_snapshot", "profile_revision_row_id", "network_topology_profile_revision", "RESTRICT"),
    ("network_topology_collection_snapshot", "payload_object_row_id", "network_topology_payload_object", "RESTRICT"),
    ("network_topology_analysis_run", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_analysis_run", "snapshot_row_id", "network_topology_collection_snapshot", "RESTRICT"),
    ("network_topology_analysis_run", "target_profile_revision_row_id", "network_topology_profile_revision", "RESTRICT"),
    ("network_topology_analysis_run", "fact_graph_payload_object_row_id", "network_topology_payload_object", "RESTRICT"),
    ("network_topology_analysis_run", "validation_report_object_row_id", "network_topology_payload_object", "RESTRICT"),
    ("network_topology_snapshot_upload_session", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_snapshot_upload_session", "manifest_row_id", "network_topology_collection_manifest", "RESTRICT"),
    ("network_topology_snapshot_upload_session", "committed_snapshot_row_id", "network_topology_collection_snapshot", "RESTRICT"),
    ("network_topology_snapshot_upload_session", "committed_analysis_run_row_id", "network_topology_analysis_run", "RESTRICT"),
    ("network_topology_observation_staging", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_observation_staging", "upload_session_row_id", "network_topology_snapshot_upload_session", "CASCADE"),
    ("network_topology_identity_registry", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_identity_registry", "first_seen_analysis_run_row_id", "network_topology_analysis_run", "RESTRICT"),
    ("network_topology_identity_registry", "last_seen_analysis_run_row_id", "network_topology_analysis_run", "RESTRICT"),
    ("network_topology_identity_alias", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_identity_alias", "identity_row_id", "network_topology_identity_registry", "RESTRICT"),
    ("network_topology_identity_alias", "first_seen_analysis_run_row_id", "network_topology_analysis_run", "RESTRICT"),
    ("network_topology_identity_alias", "last_seen_analysis_run_row_id", "network_topology_analysis_run", "RESTRICT"),
    ("network_topology_identity_conflict_audit", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_identity_conflict_audit", "analysis_run_row_id", "network_topology_analysis_run", "RESTRICT"),
    ("network_topology_identity_conflict_audit", "incumbent_identity_row_id", "network_topology_identity_registry", "RESTRICT"),
    ("network_topology_identity_conflict_audit", "candidate_identity_row_id", "network_topology_identity_registry", "RESTRICT"),
    ("network_topology_sync_alert", "topology_row_id", "network_topology", "RESTRICT"),
    ("network_topology_sync_alert", "analysis_run_row_id", "network_topology_analysis_run", "RESTRICT"),
)
# fmt: on


def _safe_identifier(prefix: str, table: str, suffix: str) -> str:
    raw = f"{prefix}_{table}_{suffix}"
    if len(raw.encode("utf-8")) <= 63:
        return raw
    digest = sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{raw[:54]}_{digest}"


def _create_table_sql(table: str, columns: tuple[str, ...]) -> str:
    body = ",\n    ".join((*_BASE_COLUMNS, *columns))
    return f'CREATE TABLE IF NOT EXISTS "{table}" (\n    {body}\n);'


def _index_sql(table: str, columns: tuple[str, ...], unique: bool) -> str:
    prefix = "uq" if unique else "idx"
    name = _safe_identifier(prefix, table, "_".join(columns))
    unique_sql = "UNIQUE " if unique else ""
    column_sql = ", ".join(f'"{column}"' for column in columns)
    return (
        f'CREATE {unique_sql}INDEX IF NOT EXISTS "{name}" '
        f'ON "{table}" ({column_sql});'
    )


def _foreign_key_sql(table: str, column: str, target: str, on_delete: str) -> str:
    name = _safe_identifier("fk", table, column)
    return f"""DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS c
        JOIN pg_class AS t ON t.oid = c.conrelid
        JOIN pg_namespace AS n ON n.oid = t.relnamespace
        WHERE c.conname = '{name}'
          AND t.relname = '{table}'
          AND n.nspname = current_schema()
    ) THEN
        ALTER TABLE "{table}"
            ADD CONSTRAINT "{name}"
            FOREIGN KEY ("{column}")
            REFERENCES "{target}" ("id")
            ON DELETE {on_delete};
    END IF;
END
$migration$;"""


_TABLE_DDL = tuple(_create_table_sql(*spec) for spec in _TABLE_COLUMNS)
_INDEX_DDL = tuple(_index_sql(*spec) for spec in _INDEX_SPECS)


async def upgrade(db: BaseDBAsyncClient) -> str:
    foreign_keys = (_foreign_key_sql(*spec) for spec in _FK_SPECS)
    return "\n\n".join((*_TABLE_DDL, _LEGACY_DDL, *_INDEX_DDL, *foreign_keys))


async def downgrade(db: BaseDBAsyncClient) -> str:
    # Phase C.3 is intentionally non-destructive in both directions.
    return "SELECT 1;"
