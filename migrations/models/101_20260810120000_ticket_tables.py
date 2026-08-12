from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

_BASE_COLUMNS = """
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18'"""


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS "ticket_type" ({_BASE_COLUMNS},
            "name" VARCHAR(64) NOT NULL,
            "code" VARCHAR(32) NOT NULL,
            "description" VARCHAR(512),
            "icon" VARCHAR(128),
            "internal" BOOL NOT NULL DEFAULT False,
            "default_flow_id" VARCHAR(36),
            "sla_config" JSONB NOT NULL DEFAULT '{{}}'
        );
        CREATE TABLE IF NOT EXISTS "ticket_flow" ({_BASE_COLUMNS},
            "name" VARCHAR(128) NOT NULL,
            "remark" VARCHAR(512),
            "internal" BOOL NOT NULL DEFAULT False,
            "ticket_type_id" VARCHAR(36) NOT NULL REFERENCES "ticket_type" ("id") ON DELETE CASCADE,
            "creator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL
        );
        -- ticket_type.default_flow_id 反向指回 ticket_flow，拆环后补约束
        ALTER TABLE "ticket_type" ADD CONSTRAINT "fk_ticket_type_default_flow"
            FOREIGN KEY ("default_flow_id") REFERENCES "ticket_flow" ("id") ON DELETE SET NULL;

        CREATE TABLE IF NOT EXISTS "ticket_flow_version" ({_BASE_COLUMNS},
            "ticket_flow_id" VARCHAR(36) NOT NULL REFERENCES "ticket_flow" ("id") ON DELETE CASCADE,
            "version" VARCHAR(32) NOT NULL,
            "change_desc" VARCHAR(512),
            "status" INT NOT NULL DEFAULT 0,
            "flow_json" JSONB NOT NULL DEFAULT '{{}}',
            "form_schema" JSONB NOT NULL DEFAULT '{{}}',
            "creator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL,
            "updator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS "ticket_flow_permission" ({_BASE_COLUMNS},
            "flow_id" VARCHAR(36) NOT NULL REFERENCES "ticket_flow" ("id") ON DELETE CASCADE,
            "users_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL,
            "roles_id" VARCHAR(36) REFERENCES "role" ("id") ON DELETE SET NULL,
            "permissions" JSONB NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS "ticket" ({_BASE_COLUMNS},
            "ticket_no" VARCHAR(64) NOT NULL,
            "title" VARCHAR(255) NOT NULL,
            "priority" INT NOT NULL DEFAULT 1,
            "status" INT NOT NULL DEFAULT 0,
            "ticket_type_id" VARCHAR(36) NOT NULL REFERENCES "ticket_type" ("id") ON DELETE CASCADE,
            "flow_version_id" VARCHAR(36) NOT NULL REFERENCES "ticket_flow_version" ("id") ON DELETE CASCADE,
            "creator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL,
            "form_data" JSONB NOT NULL DEFAULT '{{}}',
            "source" INT NOT NULL DEFAULT 0,
            "origin_run_id" VARCHAR(64),
            "origin_node_id" VARCHAR(64),
            "origin_flow_id" VARCHAR(64),
            "origin_params" JSONB,
            "callback_token" VARCHAR(64),
            "sla_respond_at" TIMESTAMPTZ,
            "sla_resolve_at" TIMESTAMPTZ,
            "closed_at" TIMESTAMPTZ,
            "suspended_at" TIMESTAMPTZ,
            "sweep_owner" VARCHAR(128),
            "sweep_expires_at" TIMESTAMPTZ
        );
        CREATE TABLE IF NOT EXISTS "ticket_link" ({_BASE_COLUMNS},
            "from_ticket_id" VARCHAR(36) NOT NULL REFERENCES "ticket" ("id") ON DELETE CASCADE,
            "to_ticket_id" VARCHAR(36) NOT NULL REFERENCES "ticket" ("id") ON DELETE CASCADE,
            "creator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS "ticket_node_instance" ({_BASE_COLUMNS},
            "ticket_id" VARCHAR(36) NOT NULL REFERENCES "ticket" ("id") ON DELETE CASCADE,
            "node_id" VARCHAR(64) NOT NULL,
            "node_name" VARCHAR(128),
            "node_type" VARCHAR(32) NOT NULL,
            "status" INT NOT NULL DEFAULT 0,
            "approve_type" INT NOT NULL DEFAULT 1,
            "results" JSONB NOT NULL DEFAULT '{{}}',
            "form_data" JSONB NOT NULL DEFAULT '{{}}',
            "deadline" TIMESTAMPTZ,
            "fork_id" VARCHAR(64),
            "branch_key" VARCHAR(64),
            "auto_run_id" VARCHAR(64),
            "entered_at" TIMESTAMPTZ,
            "completed_at" TIMESTAMPTZ
        );
        CREATE TABLE IF NOT EXISTS "ticket_activity" ({_BASE_COLUMNS},
            "ticket_id" VARCHAR(36) NOT NULL REFERENCES "ticket" ("id") ON DELETE CASCADE,
            "node_instance_id" VARCHAR(36) REFERENCES "ticket_node_instance" ("id") ON DELETE SET NULL,
            "actor_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL,
            "action" VARCHAR(32) NOT NULL,
            "content" TEXT,
            "payload" JSONB NOT NULL DEFAULT '{{}}'
        );
        CREATE TABLE IF NOT EXISTS "ticket_attachment" ({_BASE_COLUMNS},
            "ticket_id" VARCHAR(36) NOT NULL REFERENCES "ticket" ("id") ON DELETE CASCADE,
            "activity_id" VARCHAR(36) REFERENCES "ticket_activity" ("id") ON DELETE SET NULL,
            "file_name" VARCHAR(255) NOT NULL,
            "file_key" VARCHAR(512) NOT NULL,
            "file_size" BIGINT NOT NULL DEFAULT 0,
            "uploader_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS "ticket_node_approvers" (
            "ticket_node_instance_id" VARCHAR(36) NOT NULL
                REFERENCES "ticket_node_instance" ("id") ON DELETE CASCADE,
            "account_id" VARCHAR(36) NOT NULL
                REFERENCES "account" ("id") ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS "uid_ticket_tenant_no"
            ON "ticket" ("tenant_id", "ticket_no") WHERE "deleted" = FALSE;
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_ticket_node_approvers"
            ON "ticket_node_approvers" ("ticket_node_instance_id", "account_id");
        CREATE INDEX IF NOT EXISTS "idx_ticket_flow_perm_flow"
            ON "ticket_flow_permission" ("flow_id");
        CREATE INDEX IF NOT EXISTS "idx_ticket_node_deadline"
            ON "ticket_node_instance" ("deadline") WHERE "status" IN (0,4);
        CREATE INDEX IF NOT EXISTS "idx_ticket_sla_sweep"
            ON "ticket" ("status", "sla_resolve_at") WHERE "deleted" = FALSE;
        CREATE INDEX IF NOT EXISTS "idx_ticket_callback_token"
            ON "ticket" ("callback_token");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "ticket_type" DROP CONSTRAINT IF EXISTS "fk_ticket_type_default_flow";
        DROP TABLE IF EXISTS "ticket_node_approvers";
        DROP TABLE IF EXISTS "ticket_attachment";
        DROP TABLE IF EXISTS "ticket_activity";
        DROP TABLE IF EXISTS "ticket_node_instance";
        DROP TABLE IF EXISTS "ticket_link";
        DROP TABLE IF EXISTS "ticket";
        DROP TABLE IF EXISTS "ticket_flow_permission";
        DROP TABLE IF EXISTS "ticket_flow_version";
        DROP TABLE IF EXISTS "ticket_flow";
        DROP TABLE IF EXISTS "ticket_type";
    """
