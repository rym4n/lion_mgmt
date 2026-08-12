from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "supply_chain_review_issue" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "accessory_project_id" VARCHAR(36),
    "domain" VARCHAR(30) NOT NULL,
    "source_object_type" VARCHAR(80),
    "source_object_id" VARCHAR(36),
    "issue_no" VARCHAR(64) NOT NULL,
    "title" VARCHAR(255) NOT NULL,
    "description" TEXT,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "remediation_requirement" TEXT,
    "responsible_person" VARCHAR(255),
    "due_date" DATE,
    "status" VARCHAR(30) NOT NULL DEFAULT 'pending',
    "attachment_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255),
    CONSTRAINT "uid_scr_issue_tenant_no" UNIQUE ("tenant_id", "issue_no")
);
        CREATE INDEX IF NOT EXISTS "idx_scr_issue_tenant_project" ON "supply_chain_review_issue" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_issue_tenant_domain" ON "supply_chain_review_issue" ("tenant_id", "domain");
        CREATE INDEX IF NOT EXISTS "idx_scr_issue_tenant_status" ON "supply_chain_review_issue" ("tenant_id", "status");
        CREATE INDEX IF NOT EXISTS "idx_scr_issue_tenant_risk" ON "supply_chain_review_issue" ("tenant_id", "risk_level");

        CREATE TABLE IF NOT EXISTS "supply_chain_remediation_record" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "review_issue_id" VARCHAR(36) NOT NULL,
    "action_type" VARCHAR(30) NOT NULL,
    "content" TEXT,
    "attachment_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "operator" VARCHAR(255),
    "operated_at" TIMESTAMPTZ,
    "from_status" VARCHAR(30),
    "to_status" VARCHAR(30),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_remediation_tenant_issue" ON "supply_chain_remediation_record" ("tenant_id", "review_issue_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_remediation_tenant_action" ON "supply_chain_remediation_record" ("tenant_id", "action_type");

        CREATE TABLE IF NOT EXISTS "supply_chain_disposal_rule" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "trigger_type" VARCHAR(60) NOT NULL,
    "enabled" BOOL NOT NULL DEFAULT True,
    "execution_mode" VARCHAR(30) NOT NULL DEFAULT 'manual_confirm',
    "actions" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "playbook_id" VARCHAR(100),
    "action_id" VARCHAR(100),
    "input_mapping" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "require_review_issue" BOOL NOT NULL DEFAULT False,
    "risk_level" VARCHAR(20) NOT NULL DEFAULT 'medium',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_disposal_rule_tenant_area" ON "supply_chain_disposal_rule" ("tenant_id", "project_area_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_disposal_rule_tenant_project" ON "supply_chain_disposal_rule" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_disposal_rule_tenant_trigger" ON "supply_chain_disposal_rule" ("tenant_id", "trigger_type");
        CREATE INDEX IF NOT EXISTS "idx_scr_disposal_rule_tenant_enabled" ON "supply_chain_disposal_rule" ("tenant_id", "enabled");

        CREATE TABLE IF NOT EXISTS "supply_chain_disposal_task" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "rule_id" VARCHAR(36),
    "trigger_type" VARCHAR(60) NOT NULL,
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "personnel_ci_id" VARCHAR(36),
    "related_ci_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "actions" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "status" VARCHAR(30) NOT NULL DEFAULT 'pending_confirm',
    "confirm_by" VARCHAR(255),
    "confirmed_at" TIMESTAMPTZ,
    "run_id" VARCHAR(64),
    "result_message" TEXT,
    "review_issue_id" VARCHAR(36),
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_disposal_task_tenant_area" ON "supply_chain_disposal_task" ("tenant_id", "project_area_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_disposal_task_tenant_project" ON "supply_chain_disposal_task" ("tenant_id", "accessory_project_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_disposal_task_tenant_personnel" ON "supply_chain_disposal_task" ("tenant_id", "personnel_ci_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_disposal_task_tenant_status" ON "supply_chain_disposal_task" ("tenant_id", "status");
        CREATE INDEX IF NOT EXISTS "idx_scr_disposal_task_tenant_run" ON "supply_chain_disposal_task" ("tenant_id", "run_id");

        CREATE TABLE IF NOT EXISTS "supply_chain_attachment_ref" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "object_type" VARCHAR(80) NOT NULL,
    "object_id" VARCHAR(36) NOT NULL,
    "file_id" VARCHAR(255) NOT NULL,
    "file_name" VARCHAR(255) NOT NULL,
    "file_type" VARCHAR(100),
    "file_size" INT NOT NULL DEFAULT 0,
    "uploaded_by" VARCHAR(255),
    "uploaded_at" TIMESTAMPTZ,
    "remark" TEXT,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_attachment_tenant_object" ON "supply_chain_attachment_ref" ("tenant_id", "object_type", "object_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_attachment_tenant_file" ON "supply_chain_attachment_ref" ("tenant_id", "file_id");

        CREATE TABLE IF NOT EXISTS "supply_chain_notification_event" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "event_type" VARCHAR(80) NOT NULL,
    "title" VARCHAR(255) NOT NULL,
    "content" TEXT,
    "risk_level" VARCHAR(20),
    "target_type" VARCHAR(80),
    "target_id" VARCHAR(36),
    "project_area_id" VARCHAR(36),
    "accessory_project_id" VARCHAR(36),
    "link" VARCHAR(255),
    "receivers" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "receiver_roles" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
        CREATE INDEX IF NOT EXISTS "idx_scr_notify_tenant_event" ON "supply_chain_notification_event" ("tenant_id", "event_type");
        CREATE INDEX IF NOT EXISTS "idx_scr_notify_tenant_target" ON "supply_chain_notification_event" ("tenant_id", "target_type", "target_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_notify_tenant_status" ON "supply_chain_notification_event" ("tenant_id", "status");

        UPDATE "permissions_menu"
        SET "Permissions" = '["supply_chain_review:model:c","supply_chain_review:model:r","supply_chain_review:model:u","supply_chain_review:model:d","supply_chain_review:field:c","supply_chain_review:field:r","supply_chain_review:field:u","supply_chain_review:field:d","supply_chain_review:relation:c","supply_chain_review:relation:r","supply_chain_review:relation:u","supply_chain_review:relation:d","supply_chain_review:ci:c","supply_chain_review:ci:r","supply_chain_review:ci:u","supply_chain_review:ci:d","supply_chain_review:project:r","supply_chain_review:overview:r","supply_chain_review:authorization:c","supply_chain_review:authorization:r","supply_chain_review:authorization:u","supply_chain_review:authorization:d","supply_chain_review:nda:c","supply_chain_review:nda:r","supply_chain_review:nda:u","supply_chain_review:nda:d","supply_chain_review:terminal:c","supply_chain_review:terminal:r","supply_chain_review:terminal:u","supply_chain_review:terminal:d","supply_chain_review:boundary:c","supply_chain_review:boundary:r","supply_chain_review:boundary:u","supply_chain_review:boundary:d","supply_chain_review:exception:c","supply_chain_review:exception:r","supply_chain_review:exception:u","supply_chain_review:exception:d","supply_chain_review:collection:c","supply_chain_review:collection:r","supply_chain_review:collection:u","supply_chain_review:collection:d","supply_chain_review:policy:c","supply_chain_review:policy:r","supply_chain_review:policy:u","supply_chain_review:policy:d","supply_chain_review:topology:c","supply_chain_review:topology:r","supply_chain_review:topology:u","supply_chain_review:topology:d","supply_chain_review:issue:c","supply_chain_review:issue:r","supply_chain_review:issue:u","supply_chain_review:issue:d","supply_chain_review:disposal:c","supply_chain_review:disposal:r","supply_chain_review:disposal:u","supply_chain_review:disposal:d","supply_chain_review:attachment:c","supply_chain_review:attachment:r","supply_chain_review:attachment:u","supply_chain_review:attachment:d","supply_chain_review:notify:c","supply_chain_review:notify:r","supply_chain_review:notify:u","supply_chain_review:notify:d"]',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'supply-chain-review';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "supply_chain_notification_event";
        DROP TABLE IF EXISTS "supply_chain_attachment_ref";
        DROP TABLE IF EXISTS "supply_chain_disposal_task";
        DROP TABLE IF EXISTS "supply_chain_disposal_rule";
        DROP TABLE IF EXISTS "supply_chain_remediation_record";
        DROP TABLE IF EXISTS "supply_chain_review_issue";
    """
