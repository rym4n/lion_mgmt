from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "report_schedule" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "document_id" VARCHAR(36) NOT NULL REFERENCES "report_document" ("id") ON DELETE CASCADE,
    "timed_id" VARCHAR(36) REFERENCES "timed" ("id") ON DELETE SET NULL,
    "creator_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL,
    "name" VARCHAR(255) NOT NULL,
    "cron" VARCHAR(64) NOT NULL,
    "export_format" VARCHAR(8) NOT NULL DEFAULT 'docx',
    "enabled" BOOL NOT NULL DEFAULT True,
    "last_run_at" TIMESTAMPTZ,
    "last_status" VARCHAR(16),
    "last_error" TEXT,
    "next_run_at" TIMESTAMPTZ
);
        CREATE INDEX IF NOT EXISTS "idx_report_schedule_tenant_document" ON "report_schedule" ("tenant_id", "document_id");
        CREATE INDEX IF NOT EXISTS "idx_report_schedule_tenant_enabled" ON "report_schedule" ("tenant_id", "enabled");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_report_schedule_active_name" ON "report_schedule" ("tenant_id", "document_id", "name") WHERE "deleted" = False;
        INSERT INTO "permissions_menu" ("id", "ctime", "utime", "deleted", "tenant_id", "model", "description", "Permissions", "url", "bp")
        VALUES (
            'agent-report-schedule-permissions',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            False,
            '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            'agent_report_schedule',
            '汇报任务',
            '[
                {"label": "新增汇报任务权限", "value": "c"},
                {"label": "删除汇报任务权限", "value": "d"},
                {"label": "修改汇报任务权限", "value": "u"},
                {"label": "查看汇报任务权限", "value": "r"}
            ]',
            '[]',
            'agent_bp'
        )
        ON CONFLICT ("model") DO UPDATE SET
            "Permissions" = EXCLUDED."Permissions",
            "description" = EXCLUDED."description",
            "bp" = EXCLUDED."bp",
            "utime" = CURRENT_TIMESTAMP;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "permissions_menu" WHERE "model" = 'agent_report_schedule';
        DROP TABLE IF EXISTS "report_schedule";"""
