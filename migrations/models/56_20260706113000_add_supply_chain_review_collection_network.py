from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "supply_chain_collection_task" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "domain" VARCHAR(30) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "execution_mode" VARCHAR(30) NOT NULL DEFAULT 'manual',
    "playbook_id" VARCHAR(100),
    "playbook_version_id" VARCHAR(100),
    "action_id" VARCHAR(100),
    "action_name" VARCHAR(255),
    "timed_task_id" VARCHAR(100),
    "event_ingestion_id" VARCHAR(100),
    "target_type" VARCHAR(50),
    "target_ci_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "credential_ref" VARCHAR(255),
    "input_params" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "parser_template_id" VARCHAR(36),
    "mapping_template_id" VARCHAR(36),
    "schedule_enabled" BOOL NOT NULL DEFAULT False,
    "cron_expression" VARCHAR(100),
    "after_success_action" VARCHAR(30) NOT NULL DEFAULT 'none',
    "enabled" BOOL NOT NULL DEFAULT True,
    "last_run_at" TIMESTAMPTZ,
    "last_run_status" VARCHAR(20),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_task_tenant_domain" ON "supply_chain_collection_task" ("tenant_id", "domain");
        CREATE INDEX IF NOT EXISTS "idx_scr_task_tenant_project" ON "supply_chain_collection_task" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_task_tenant_enabled" ON "supply_chain_collection_task" ("tenant_id", "enabled");

        CREATE TABLE IF NOT EXISTS "supply_chain_collection_run" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "task_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "domain" VARCHAR(30) NOT NULL,
    "run_id" VARCHAR(64) NOT NULL,
    "playbook_run_id" VARCHAR(100),
    "action_run_id" VARCHAR(100),
    "timed_run_id" VARCHAR(100),
    "event_batch_id" VARCHAR(100),
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "started_at" TIMESTAMPTZ,
    "finished_at" TIMESTAMPTZ,
    "raw_payload_ref" VARCHAR(255),
    "parsed_result_ref" VARCHAR(255),
    "total_count" INT NOT NULL DEFAULT 0,
    "success_count" INT NOT NULL DEFAULT 0,
    "failed_count" INT NOT NULL DEFAULT 0,
    "error_message" TEXT,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_run_tenant_task" ON "supply_chain_collection_run" ("tenant_id", "task_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_run_tenant_domain" ON "supply_chain_collection_run" ("tenant_id", "domain");
        CREATE INDEX IF NOT EXISTS "idx_scr_run_tenant_project" ON "supply_chain_collection_run" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_run_tenant_run_id" ON "supply_chain_collection_run" ("tenant_id", "run_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_run_tenant_status" ON "supply_chain_collection_run" ("tenant_id", "status");

        CREATE TABLE IF NOT EXISTS "supply_chain_parse_mapping_template" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "domain" VARCHAR(30) NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "source_format" VARCHAR(20) NOT NULL DEFAULT 'json',
    "parser_type" VARCHAR(20) NOT NULL DEFAULT 'builtin',
    "parser_rules" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "mapping_rules" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "enabled" BOOL NOT NULL DEFAULT True,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_template_tenant_domain" ON "supply_chain_parse_mapping_template" ("tenant_id", "domain");
        CREATE INDEX IF NOT EXISTS "idx_scr_template_tenant_enabled" ON "supply_chain_parse_mapping_template" ("tenant_id", "enabled");

        CREATE TABLE IF NOT EXISTS "supply_chain_preset_policy" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "device_ci_id" VARCHAR(36),
    "rule_name" VARCHAR(255) NOT NULL,
    "source_zone" VARCHAR(100),
    "source_address" VARCHAR(255),
    "target_zone" VARCHAR(100),
    "target_address" VARCHAR(255),
    "protocol" VARCHAR(30),
    "port" VARCHAR(100),
    "action" VARCHAR(20) NOT NULL DEFAULT 'permit',
    "business_purpose" TEXT,
    "approval_status" VARCHAR(30),
    "effective_status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "valid_from" DATE,
    "valid_to" DATE,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_preset_tenant_project" ON "supply_chain_preset_policy" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_preset_tenant_device" ON "supply_chain_preset_policy" ("tenant_id", "device_ci_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_preset_tenant_status" ON "supply_chain_preset_policy" ("tenant_id", "effective_status");

        CREATE TABLE IF NOT EXISTS "supply_chain_actual_policy" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "device_ci_id" VARCHAR(36),
    "collection_run_id" VARCHAR(36),
    "raw_rule_id" VARCHAR(255),
    "rule_name" VARCHAR(255),
    "source_zone" VARCHAR(100),
    "source_address" VARCHAR(255),
    "target_zone" VARCHAR(100),
    "target_address" VARCHAR(255),
    "protocol" VARCHAR(30),
    "port" VARCHAR(100),
    "action" VARCHAR(20) NOT NULL DEFAULT 'permit',
    "raw_text" TEXT,
    "parse_status" VARCHAR(20) NOT NULL DEFAULT 'parsed',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_actual_tenant_project" ON "supply_chain_actual_policy" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_actual_tenant_device" ON "supply_chain_actual_policy" ("tenant_id", "device_ci_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_actual_tenant_run" ON "supply_chain_actual_policy" ("tenant_id", "collection_run_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_actual_tenant_parse" ON "supply_chain_actual_policy" ("tenant_id", "parse_status");

        CREATE TABLE IF NOT EXISTS "supply_chain_policy_compare_run" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "collection_run_id" VARCHAR(36),
    "total" INT NOT NULL DEFAULT 0,
    "matched" INT NOT NULL DEFAULT 0,
    "added" INT NOT NULL DEFAULT 0,
    "missing" INT NOT NULL DEFAULT 0,
    "changed" INT NOT NULL DEFAULT 0,
    "unparsed" INT NOT NULL DEFAULT 0,
    "started_at" TIMESTAMPTZ,
    "finished_at" TIMESTAMPTZ,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_compare_run_tenant_project" ON "supply_chain_policy_compare_run" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_compare_run_tenant_collection" ON "supply_chain_policy_compare_run" ("tenant_id", "collection_run_id");

        CREATE TABLE IF NOT EXISTS "supply_chain_policy_compare_item" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "compare_run_id" VARCHAR(36) NOT NULL,
    "preset_policy_id" VARCHAR(36),
    "actual_policy_id" VARCHAR(36),
    "compare_type" VARCHAR(20) NOT NULL,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "handle_status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "review_issue_id" VARCHAR(36),
    "diff" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_compare_item_tenant_run" ON "supply_chain_policy_compare_item" ("tenant_id", "compare_run_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_compare_item_tenant_type" ON "supply_chain_policy_compare_item" ("tenant_id", "compare_type");
        CREATE INDEX IF NOT EXISTS "idx_scr_compare_item_tenant_status" ON "supply_chain_policy_compare_item" ("tenant_id", "handle_status");

        CREATE TABLE IF NOT EXISTS "supply_chain_policy_change" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "device_ci_id" VARCHAR(36),
    "policy_name" VARCHAR(255),
    "change_type" VARCHAR(20) NOT NULL,
    "diff_text" TEXT,
    "detected_at" TIMESTAMPTZ,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'high',
    "external_approve_ref" VARCHAR(100),
    "external_approve_result" VARCHAR(100),
    "review_issue_id" VARCHAR(36),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_policy_change_tenant_project" ON "supply_chain_policy_change" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_policy_change_tenant_device" ON "supply_chain_policy_change" ("tenant_id", "device_ci_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_policy_change_tenant_type" ON "supply_chain_policy_change" ("tenant_id", "change_type");
        CREATE INDEX IF NOT EXISTS "idx_scr_policy_change_tenant_detected" ON "supply_chain_policy_change" ("tenant_id", "detected_at");

        CREATE TABLE IF NOT EXISTS "supply_chain_network_topology" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "name" VARCHAR(255) NOT NULL,
    "version" VARCHAR(50) NOT NULL DEFAULT 'v1',
    "graph_data" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "change_summary" TEXT,
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_topology_tenant_area" ON "supply_chain_network_topology" ("tenant_id", "project_area_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_topology_tenant_project" ON "supply_chain_network_topology" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_topology_tenant_status" ON "supply_chain_network_topology" ("tenant_id", "status");

        UPDATE "permissions_menu"
        SET "Permissions" = '["supply_chain_review:model:c","supply_chain_review:model:r","supply_chain_review:model:u","supply_chain_review:model:d","supply_chain_review:field:c","supply_chain_review:field:r","supply_chain_review:field:u","supply_chain_review:field:d","supply_chain_review:relation:c","supply_chain_review:relation:r","supply_chain_review:relation:u","supply_chain_review:relation:d","supply_chain_review:ci:c","supply_chain_review:ci:r","supply_chain_review:ci:u","supply_chain_review:ci:d","supply_chain_review:project:r","supply_chain_review:overview:r","supply_chain_review:authorization:c","supply_chain_review:authorization:r","supply_chain_review:authorization:u","supply_chain_review:authorization:d","supply_chain_review:nda:c","supply_chain_review:nda:r","supply_chain_review:nda:u","supply_chain_review:nda:d","supply_chain_review:terminal:c","supply_chain_review:terminal:r","supply_chain_review:terminal:u","supply_chain_review:terminal:d","supply_chain_review:boundary:c","supply_chain_review:boundary:r","supply_chain_review:boundary:u","supply_chain_review:boundary:d","supply_chain_review:exception:c","supply_chain_review:exception:r","supply_chain_review:exception:u","supply_chain_review:exception:d","supply_chain_review:collection:c","supply_chain_review:collection:r","supply_chain_review:collection:u","supply_chain_review:collection:d","supply_chain_review:policy:c","supply_chain_review:policy:r","supply_chain_review:policy:u","supply_chain_review:policy:d","supply_chain_review:topology:c","supply_chain_review:topology:r","supply_chain_review:topology:u","supply_chain_review:topology:d"]',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'supply-chain-review';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "supply_chain_network_topology";
        DROP TABLE IF EXISTS "supply_chain_policy_change";
        DROP TABLE IF EXISTS "supply_chain_policy_compare_item";
        DROP TABLE IF EXISTS "supply_chain_policy_compare_run";
        DROP TABLE IF EXISTS "supply_chain_actual_policy";
        DROP TABLE IF EXISTS "supply_chain_preset_policy";
        DROP TABLE IF EXISTS "supply_chain_parse_mapping_template";
        DROP TABLE IF EXISTS "supply_chain_collection_run";
        DROP TABLE IF EXISTS "supply_chain_collection_task";
    """
