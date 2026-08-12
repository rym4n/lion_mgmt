from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "supply_chain_project_authorization" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "personnel_ci_id" VARCHAR(36) NOT NULL,
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "secrecy_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "project_role" VARCHAR(100),
    "authorized_from" DATE,
    "authorized_to" DATE,
    "access_control_status" VARCHAR(20) NOT NULL DEFAULT 'normal',
    "status" VARCHAR(20) NOT NULL DEFAULT 'active',
    "remark" TEXT,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_auth_tenant_area" ON "supply_chain_project_authorization" ("tenant_id", "project_area_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_auth_tenant_project" ON "supply_chain_project_authorization" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_auth_tenant_personnel" ON "supply_chain_project_authorization" ("tenant_id", "personnel_ci_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_auth_tenant_status" ON "supply_chain_project_authorization" ("tenant_id", "status");

        CREATE TABLE IF NOT EXISTS "supply_chain_nda_record" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "personnel_ci_id" VARCHAR(36) NOT NULL,
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "nda_type" VARCHAR(50),
    "sign_status" VARCHAR(20) NOT NULL DEFAULT 'unsigned',
    "signed_at" TIMESTAMPTZ,
    "expires_at" TIMESTAMPTZ,
    "evidence_attachment_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "source_type" VARCHAR(30) NOT NULL DEFAULT 'manual',
    "source_record_id" VARCHAR(100),
    "last_sync_at" TIMESTAMPTZ,
    "remark" TEXT,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_nda_tenant_area" ON "supply_chain_nda_record" ("tenant_id", "project_area_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_nda_tenant_project" ON "supply_chain_nda_record" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_nda_tenant_personnel" ON "supply_chain_nda_record" ("tenant_id", "personnel_ci_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_nda_tenant_status" ON "supply_chain_nda_record" ("tenant_id", "sign_status");
        CREATE INDEX IF NOT EXISTS "idx_scr_nda_tenant_expires" ON "supply_chain_nda_record" ("tenant_id", "expires_at");

        CREATE TABLE IF NOT EXISTS "supply_chain_unknown_terminal" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "mac_address" VARCHAR(64) NOT NULL,
    "ip_address" VARCHAR(64),
    "network_device_ci_id" VARCHAR(36),
    "port" VARCHAR(100),
    "first_seen_at" TIMESTAMPTZ,
    "last_seen_at" TIMESTAMPTZ,
    "discovery_source" VARCHAR(30) NOT NULL DEFAULT 'switch_mac',
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "endpoint_ci_id" VARCHAR(36),
    "review_issue_id" VARCHAR(36),
    "remark" TEXT,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_unknown_tenant_project" ON "supply_chain_unknown_terminal" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_unknown_tenant_mac" ON "supply_chain_unknown_terminal" ("tenant_id", "mac_address");
        CREATE INDEX IF NOT EXISTS "idx_scr_unknown_tenant_status" ON "supply_chain_unknown_terminal" ("tenant_id", "status");
        CREATE INDEX IF NOT EXISTS "idx_scr_unknown_tenant_seen" ON "supply_chain_unknown_terminal" ("tenant_id", "last_seen_at");

        CREATE TABLE IF NOT EXISTS "supply_chain_boundary_log" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "log_category" VARCHAR(30) NOT NULL,
    "operator_account" VARCHAR(255),
    "client_address" VARCHAR(100),
    "personnel_ci_id" VARCHAR(36),
    "endpoint_ci_id" VARCHAR(36),
    "occurred_at" TIMESTAMPTZ,
    "source_type" VARCHAR(30) NOT NULL DEFAULT 'manual',
    "risk_flag" BOOL NOT NULL DEFAULT False,
    "detail" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_boundary_tenant_project" ON "supply_chain_boundary_log" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_boundary_tenant_occurred" ON "supply_chain_boundary_log" ("tenant_id", "occurred_at");
        CREATE INDEX IF NOT EXISTS "idx_scr_boundary_project_occurred" ON "supply_chain_boundary_log" ("tenant_id", "accessory_project_id", "occurred_at");
        CREATE INDEX IF NOT EXISTS "idx_scr_boundary_tenant_risk" ON "supply_chain_boundary_log" ("tenant_id", "risk_flag");
        CREATE INDEX IF NOT EXISTS "idx_scr_boundary_tenant_category" ON "supply_chain_boundary_log" ("tenant_id", "log_category");

        CREATE TABLE IF NOT EXISTS "supply_chain_exception_device_log" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "exception_device_ci_id" VARCHAR(36) NOT NULL,
    "endpoint_ci_id" VARCHAR(36),
    "personnel_ci_id" VARCHAR(36),
    "log_type" VARCHAR(20) NOT NULL DEFAULT 'use',
    "detail" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "occurred_at" TIMESTAMPTZ,
    "source_type" VARCHAR(30) NOT NULL DEFAULT 'manual',
    "abnormal_type" VARCHAR(40) NOT NULL DEFAULT 'none',
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "review_issue_id" VARCHAR(36),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_exception_tenant_project" ON "supply_chain_exception_device_log" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_exception_tenant_device" ON "supply_chain_exception_device_log" ("tenant_id", "exception_device_ci_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_exception_tenant_personnel" ON "supply_chain_exception_device_log" ("tenant_id", "personnel_ci_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_exception_tenant_occurred" ON "supply_chain_exception_device_log" ("tenant_id", "occurred_at");
        CREATE INDEX IF NOT EXISTS "idx_scr_exception_project_occurred" ON "supply_chain_exception_device_log" ("tenant_id", "accessory_project_id", "occurred_at");
        CREATE INDEX IF NOT EXISTS "idx_scr_exception_tenant_abnormal" ON "supply_chain_exception_device_log" ("tenant_id", "abnormal_type");
        CREATE INDEX IF NOT EXISTS "idx_scr_exception_tenant_status" ON "supply_chain_exception_device_log" ("tenant_id", "status");

        UPDATE "permissions_menu"
        SET "Permissions" = '["supply_chain_review:model:c","supply_chain_review:model:r","supply_chain_review:model:u","supply_chain_review:model:d","supply_chain_review:field:c","supply_chain_review:field:r","supply_chain_review:field:u","supply_chain_review:field:d","supply_chain_review:relation:c","supply_chain_review:relation:r","supply_chain_review:relation:u","supply_chain_review:relation:d","supply_chain_review:ci:c","supply_chain_review:ci:r","supply_chain_review:ci:u","supply_chain_review:ci:d","supply_chain_review:project:r","supply_chain_review:overview:r","supply_chain_review:authorization:c","supply_chain_review:authorization:r","supply_chain_review:authorization:u","supply_chain_review:authorization:d","supply_chain_review:nda:c","supply_chain_review:nda:r","supply_chain_review:nda:u","supply_chain_review:nda:d","supply_chain_review:terminal:c","supply_chain_review:terminal:r","supply_chain_review:terminal:u","supply_chain_review:terminal:d","supply_chain_review:boundary:c","supply_chain_review:boundary:r","supply_chain_review:boundary:u","supply_chain_review:boundary:d","supply_chain_review:exception:c","supply_chain_review:exception:r","supply_chain_review:exception:u","supply_chain_review:exception:d"]',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'supply-chain-review';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "supply_chain_exception_device_log";
        DROP TABLE IF EXISTS "supply_chain_boundary_log";
        DROP TABLE IF EXISTS "supply_chain_unknown_terminal";
        DROP TABLE IF EXISTS "supply_chain_nda_record";
        DROP TABLE IF EXISTS "supply_chain_project_authorization";
    """
