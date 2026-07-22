from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    """Rebuild the unpublished asset-management schema on its v1 contract."""
    return r"""
        DROP TABLE IF EXISTS "supply_chain_builtin_catalog_event";
        DROP TABLE IF EXISTS "supply_chain_builtin_activation_event";
        DROP TABLE IF EXISTS "supply_chain_builtin_activation_preview";
        DROP TABLE IF EXISTS "supply_chain_builtin_activation";
        DROP TABLE IF EXISTS "supply_chain_builtin_catalog_state";
        DROP TABLE IF EXISTS "supply_chain_ci_relation";
        DROP TABLE IF EXISTS "supply_chain_ci";
        DROP TABLE IF EXISTS "supply_chain_ci_relation_type";
        DROP TABLE IF EXISTS "supply_chain_ci_unique_rule";
        DROP TABLE IF EXISTS "supply_chain_ci_field_def";
        DROP TABLE IF EXISTS "supply_chain_ci_type";
        DROP TABLE IF EXISTS "supply_chain_ci_model_group";

        CREATE TABLE "supply_chain_ci_model_group" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "key" VARCHAR(64) NOT NULL,
            "name" VARCHAR(255) NOT NULL,
            "sort" INT NOT NULL DEFAULT 0,
            "is_builtin" BOOL NOT NULL DEFAULT FALSE,
            "is_locked" BOOL NOT NULL DEFAULT FALSE,
            "builtin_version" VARCHAR(64),
            "builtin_hash" VARCHAR(64),
            "creator" VARCHAR(255),
            "updater" VARCHAR(255),
            CONSTRAINT "uid_ci_model_group_tenant_key" UNIQUE ("tenant_id", "key")
        );
        CREATE INDEX "idx_ci_model_group_tenant_sort"
            ON "supply_chain_ci_model_group" ("tenant_id", "sort");
        CREATE INDEX "idx_ci_model_group_tenant_builtin"
            ON "supply_chain_ci_model_group" ("tenant_id", "is_builtin");

        CREATE TABLE "supply_chain_ci_type" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "key" VARCHAR(64) NOT NULL,
            "name" VARCHAR(255) NOT NULL,
            "icon" VARCHAR(100),
            "group_id" VARCHAR(36) NOT NULL,
            "group_key" VARCHAR(64) NOT NULL,
            "primary_field" VARCHAR(64) NOT NULL DEFAULT '',
            "status" VARCHAR(20) NOT NULL DEFAULT 'active',
            "is_locked" BOOL NOT NULL DEFAULT FALSE,
            "is_builtin" BOOL NOT NULL DEFAULT FALSE,
            "builtin_key" VARCHAR(64),
            "builtin_version" VARCHAR(64),
            "builtin_hash" VARCHAR(64),
            "owner" VARCHAR(20) NOT NULL DEFAULT 'tenant',
            "sort" INT NOT NULL DEFAULT 0,
            "creator" VARCHAR(255),
            "updater" VARCHAR(255),
            CONSTRAINT "uid_ci_type_tenant_key" UNIQUE ("tenant_id", "key"),
            CONSTRAINT "fk_ci_type_group" FOREIGN KEY ("group_id")
                REFERENCES "supply_chain_ci_model_group" ("id") ON DELETE RESTRICT
        );
        CREATE INDEX "idx_ci_type_tenant_status"
            ON "supply_chain_ci_type" ("tenant_id", "status");
        CREATE INDEX "idx_ci_type_tenant_group_sort"
            ON "supply_chain_ci_type" ("tenant_id", "group_id", "sort");
        CREATE INDEX "idx_ci_type_tenant_builtin"
            ON "supply_chain_ci_type" ("tenant_id", "is_builtin");

        CREATE TABLE "supply_chain_ci_field_def" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "ci_type_id" VARCHAR(36) NOT NULL,
            "ci_type_key" VARCHAR(64) NOT NULL,
            "field_key" VARCHAR(64) NOT NULL,
            "source_field_key" VARCHAR(64),
            "label" VARCHAR(255) NOT NULL,
            "data_type" VARCHAR(20) NOT NULL,
            "enum_options" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "options" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "ref_ci_type_id" VARCHAR(36),
            "ref_ci_type" VARCHAR(64),
            "required" BOOL NOT NULL DEFAULT FALSE,
            "unique" BOOL NOT NULL DEFAULT FALSE,
            "editable" BOOL NOT NULL DEFAULT TRUE,
            "is_derived" BOOL NOT NULL DEFAULT FALSE,
            "field_group" VARCHAR(255) NOT NULL DEFAULT '其他',
            "field_group_sort" INT NOT NULL DEFAULT 0,
            "user_prompt" TEXT,
            "default_value" JSONB,
            "sort" INT NOT NULL DEFAULT 0,
            "is_locked" BOOL NOT NULL DEFAULT FALSE,
            "is_builtin" BOOL NOT NULL DEFAULT FALSE,
            "builtin_key" VARCHAR(160),
            "builtin_version" VARCHAR(64),
            "builtin_hash" VARCHAR(64),
            "owner" VARCHAR(20) NOT NULL DEFAULT 'tenant',
            "creator" VARCHAR(255),
            "updater" VARCHAR(255),
            CONSTRAINT "uid_ci_field_tenant_type_key"
                UNIQUE ("tenant_id", "ci_type_id", "field_key"),
            CONSTRAINT "fk_ci_field_model" FOREIGN KEY ("ci_type_id")
                REFERENCES "supply_chain_ci_type" ("id") ON DELETE RESTRICT,
            CONSTRAINT "fk_ci_field_ref_model" FOREIGN KEY ("ref_ci_type_id")
                REFERENCES "supply_chain_ci_type" ("id") ON DELETE RESTRICT
        );
        CREATE INDEX "idx_ci_field_tenant_type"
            ON "supply_chain_ci_field_def" ("tenant_id", "ci_type_id");
        CREATE INDEX "idx_ci_field_tenant_data_type"
            ON "supply_chain_ci_field_def" ("tenant_id", "data_type");
        CREATE INDEX "idx_ci_field_group"
            ON "supply_chain_ci_field_def" ("tenant_id", "ci_type_id", "field_group_sort");

        CREATE TABLE "supply_chain_ci_unique_rule" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "ci_type_id" VARCHAR(36) NOT NULL,
            "ci_type_key" VARCHAR(64) NOT NULL,
            "key" VARCHAR(64) NOT NULL,
            "field_keys" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "sort" INT NOT NULL DEFAULT 0,
            "is_locked" BOOL NOT NULL DEFAULT FALSE,
            "is_builtin" BOOL NOT NULL DEFAULT FALSE,
            "builtin_key" VARCHAR(160),
            "builtin_version" VARCHAR(64),
            "builtin_hash" VARCHAR(64),
            "owner" VARCHAR(20) NOT NULL DEFAULT 'tenant',
            "creator" VARCHAR(255),
            "updater" VARCHAR(255),
            CONSTRAINT "uid_ci_unique_rule_tenant_type_key"
                UNIQUE ("tenant_id", "ci_type_id", "key"),
            CONSTRAINT "fk_ci_unique_rule_model" FOREIGN KEY ("ci_type_id")
                REFERENCES "supply_chain_ci_type" ("id") ON DELETE RESTRICT
        );
        CREATE INDEX "idx_ci_unique_rule_tenant_type"
            ON "supply_chain_ci_unique_rule" ("tenant_id", "ci_type_id");

        CREATE TABLE "supply_chain_ci_relation_type" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "key" VARCHAR(64) NOT NULL,
            "name" VARCHAR(255) NOT NULL,
            "source_ci_type_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "target_ci_type_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "source_ci_types" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "target_ci_types" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "source_to_target" VARCHAR(10) NOT NULL DEFAULT 'many',
            "target_to_source" VARCHAR(10) NOT NULL DEFAULT 'many',
            "semantic_key" VARCHAR(64),
            "relation_fields" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "is_locked" BOOL NOT NULL DEFAULT FALSE,
            "is_builtin" BOOL NOT NULL DEFAULT FALSE,
            "builtin_key" VARCHAR(160),
            "builtin_version" VARCHAR(64),
            "builtin_hash" VARCHAR(64),
            "owner" VARCHAR(20) NOT NULL DEFAULT 'tenant',
            "creator" VARCHAR(255),
            "updater" VARCHAR(255),
            CONSTRAINT "uid_ci_relation_type_tenant_key" UNIQUE ("tenant_id", "key"),
            CONSTRAINT "chk_ci_relation_source_quantity"
                CHECK ("source_to_target" IN ('one', 'many')),
            CONSTRAINT "chk_ci_relation_target_quantity"
                CHECK ("target_to_source" IN ('one', 'many'))
        );

        CREATE TABLE "supply_chain_ci" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "ci_type_id" VARCHAR(36) NOT NULL,
            "ci_type_key" VARCHAR(64) NOT NULL,
            "name" VARCHAR(255) NOT NULL,
            "project_area_id" VARCHAR(36),
            "accessory_project_id" VARCHAR(36),
            "attributes" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "status" VARCHAR(20) NOT NULL DEFAULT 'active',
            "risk_status" VARCHAR(20) NOT NULL DEFAULT 'normal',
            "creator" VARCHAR(255),
            "updater" VARCHAR(255),
            CONSTRAINT "fk_ci_model" FOREIGN KEY ("ci_type_id")
                REFERENCES "supply_chain_ci_type" ("id") ON DELETE RESTRICT
        );
        CREATE INDEX "idx_ci_tenant_type" ON "supply_chain_ci" ("tenant_id", "ci_type_id");
        CREATE INDEX "idx_ci_tenant_type_status"
            ON "supply_chain_ci" ("tenant_id", "ci_type_id", "status");
        CREATE INDEX "idx_ci_tenant_area" ON "supply_chain_ci" ("tenant_id", "project_area_id");
        CREATE INDEX "idx_ci_tenant_project"
            ON "supply_chain_ci" ("tenant_id", "accessory_project_id");
        CREATE INDEX "idx_ci_scope"
            ON "supply_chain_ci" ("tenant_id", "ci_type_id", "project_area_id", "accessory_project_id");
        CREATE INDEX "idx_ci_attributes_gin" ON "supply_chain_ci" USING GIN ("attributes");

        CREATE TABLE "supply_chain_ci_relation" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "relation_type_key" VARCHAR(64) NOT NULL,
            "source_ci_id" VARCHAR(36) NOT NULL,
            "target_ci_id" VARCHAR(36) NOT NULL,
            "attributes" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "creator" VARCHAR(255),
            "updater" VARCHAR(255),
            CONSTRAINT "fk_ci_relation_source" FOREIGN KEY ("source_ci_id")
                REFERENCES "supply_chain_ci" ("id") ON DELETE CASCADE,
            CONSTRAINT "fk_ci_relation_target" FOREIGN KEY ("target_ci_id")
                REFERENCES "supply_chain_ci" ("id") ON DELETE CASCADE,
            CONSTRAINT "uid_ci_relation_tenant_identity"
                UNIQUE ("tenant_id", "relation_type_key", "source_ci_id", "target_ci_id")
        );
        CREATE INDEX "idx_ci_relation_tenant_type"
            ON "supply_chain_ci_relation" ("tenant_id", "relation_type_key");
        CREATE INDEX "idx_ci_relation_source"
            ON "supply_chain_ci_relation" ("tenant_id", "source_ci_id");
        CREATE INDEX "idx_ci_relation_target"
            ON "supply_chain_ci_relation" ("tenant_id", "target_ci_id");

        CREATE TABLE "supply_chain_builtin_catalog_state" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "catalog_key" VARCHAR(64) NOT NULL,
            "catalog_version" VARCHAR(64) NOT NULL,
            "catalog_digest" VARCHAR(80) NOT NULL,
            "revision" INT NOT NULL DEFAULT 0,
            "status" VARCHAR(32) NOT NULL DEFAULT 'ready',
            "onboarding_completed" BOOL NOT NULL DEFAULT FALSE,
            "updater" VARCHAR(255),
            CONSTRAINT "uid_builtin_catalog_tenant_key" UNIQUE ("tenant_id", "catalog_key")
        );
        CREATE INDEX "idx_builtin_catalog_tenant_status"
            ON "supply_chain_builtin_catalog_state" ("tenant_id", "status");

        CREATE TABLE "supply_chain_builtin_catalog_event" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "request_id" VARCHAR(64) NOT NULL,
            "action" VARCHAR(20) NOT NULL DEFAULT 'reconcile',
            "status" VARCHAR(20) NOT NULL,
            "catalog_version" VARCHAR(64) NOT NULL,
            "catalog_digest" VARCHAR(80) NOT NULL,
            "before_state" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "after_state" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "changes" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "failure_reason" VARCHAR(255),
            "operator" VARCHAR(255),
            CONSTRAINT "uid_builtin_catalog_event_request"
                UNIQUE ("tenant_id", "request_id")
        );
        CREATE INDEX "idx_builtin_catalog_event_ctime"
            ON "supply_chain_builtin_catalog_event" ("tenant_id", "ctime");
        CREATE INDEX "idx_builtin_catalog_event_status"
            ON "supply_chain_builtin_catalog_event" ("tenant_id", "status");

        CREATE TABLE "supply_chain_builtin_activation" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "subject_type" VARCHAR(20) NOT NULL DEFAULT 'model',
            "subject_key" VARCHAR(64) NOT NULL,
            "enabled" BOOL NOT NULL DEFAULT FALSE,
            "revision" INT NOT NULL DEFAULT 0,
            "updater" VARCHAR(255),
            CONSTRAINT "uid_builtin_activation_tenant_subject"
                UNIQUE ("tenant_id", "subject_type", "subject_key")
        );
        CREATE INDEX "idx_builtin_activation_tenant_enabled"
            ON "supply_chain_builtin_activation" ("tenant_id", "enabled");

        CREATE TABLE "supply_chain_builtin_activation_preview" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "token" VARCHAR(64) NOT NULL,
            "action" VARCHAR(16) NOT NULL,
            "scope" VARCHAR(16) NOT NULL,
            "subject_keys" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "catalog_version" VARCHAR(64) NOT NULL,
            "tenant_revision" INT NOT NULL DEFAULT 0,
            "impact" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "expires_at" TIMESTAMPTZ NOT NULL,
            "used" BOOL NOT NULL DEFAULT FALSE,
            "creator" VARCHAR(255),
            CONSTRAINT "uid_builtin_preview_tenant_token" UNIQUE ("tenant_id", "token")
        );
        CREATE INDEX "idx_builtin_preview_tenant_expiry"
            ON "supply_chain_builtin_activation_preview" ("tenant_id", "expires_at");

        CREATE TABLE "supply_chain_builtin_activation_event" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255),
            "request_id" VARCHAR(64) NOT NULL,
            "action" VARCHAR(16) NOT NULL,
            "scope" VARCHAR(16) NOT NULL,
            "subject_keys" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "before_state" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "after_state" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "impact" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "catalog_version" VARCHAR(64) NOT NULL,
            "tenant_revision" INT NOT NULL DEFAULT 0,
            "operator" VARCHAR(255),
            CONSTRAINT "uid_builtin_event_tenant_request" UNIQUE ("tenant_id", "request_id")
        );
        CREATE INDEX "idx_builtin_event_tenant_ctime"
            ON "supply_chain_builtin_activation_event" ("tenant_id", "ctime");

        UPDATE "permissions_menu"
        SET "Permissions" = COALESCE("Permissions", '[]'::jsonb)
            || '["supply_chain_review:builtin-activation:u","supply_chain_review:builtin-catalog:admin"]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'supply-chain-review';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return r"""
        SELECT 1;
    """
