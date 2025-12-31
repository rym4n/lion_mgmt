from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "playbook_webhook" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "webhook_token" VARCHAR(16) NOT NULL UNIQUE,
    "description" TEXT,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_version_id" VARCHAR(36) NOT NULL REFERENCES "playbook_version" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "playbook_webhook"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook_webhook"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook_webhook"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook_webhook"."webhook_token" IS 'webhook唯一标识令牌';
COMMENT ON COLUMN "playbook_webhook"."description" IS '描述';
COMMENT ON COLUMN "playbook_webhook"."creator_id" IS '创建人';
COMMENT ON COLUMN "playbook_webhook"."playbook_version_id" IS '剧本版本';
COMMENT ON TABLE "playbook_webhook" IS '剧本Webhook模型';
        ALTER TABLE "approvement" ADD "callback_token" VARCHAR(16) NOT NULL;
        ALTER TABLE "approvement" ALTER COLUMN "params" DROP DEFAULT;
        ALTER TABLE "approvement" ALTER COLUMN "params" DROP NOT NULL;
        ALTER TABLE "event_related_script" ADD "concurrency" INT;
        ALTER TABLE "events" ADD "download_url" VARCHAR(255) NOT NULL;
        ALTER TABLE "events_ingestion" ADD "project_id" INT UNIQUE;
        ALTER TABLE "events_ingestion" ALTER COLUMN "vector_config" DROP DEFAULT;
        ALTER TABLE "tool_resources" ALTER COLUMN "resources" TYPE VARCHAR(5000) USING "resources"::VARCHAR(5000);
        COMMENT ON COLUMN "approvement"."callback_token" IS '外部回调token';
COMMENT ON COLUMN "event_related_script"."concurrency" IS '并行数量';
COMMENT ON COLUMN "events"."download_url" IS '事件报告下载链接';
COMMENT ON COLUMN "events_ingestion"."project_id" IS '关联VL的项目id';
        DROP TABLE IF EXISTS "audit_log";
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_events_inge_vector__dd2165" ON "events_ingestion" ("vector_name");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_events_inge_syslog__8aa9f5" ON "events_ingestion" ("syslog_config");
        CREATE INDEX IF NOT EXISTS "idx_runlog_playboo_d168c6" ON "runlog" ("playbook_name", "run_id", "status");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" DROP CONSTRAINT IF EXISTS "events_ingestion_syslog_config_key";
        DROP INDEX IF EXISTS "uid_events_inge_syslog__8aa9f5";
        ALTER TABLE "events_ingestion" DROP CONSTRAINT IF EXISTS "events_ingestion_vector_name_key";
        DROP INDEX IF EXISTS "uid_events_inge_vector__dd2165";
        DROP INDEX IF EXISTS "uid_events_inge_project_6e86c2";
        DROP INDEX IF EXISTS "idx_runlog_playboo_d168c6";
        ALTER TABLE "events" DROP COLUMN "download_url";
        ALTER TABLE "approvement" DROP COLUMN "callback_token";
        ALTER TABLE "approvement" ALTER COLUMN "params" SET NOT NULL;
        ALTER TABLE "approvement" ALTER COLUMN "params" SET;
        ALTER TABLE "tool_resources" ALTER COLUMN "resources" TYPE VARCHAR(3000) USING "resources"::VARCHAR(3000);
        ALTER TABLE "events_ingestion" DROP COLUMN "project_id";
        ALTER TABLE "events_ingestion" ALTER COLUMN "vector_config" SET;
        ALTER TABLE "event_related_script" DROP COLUMN "concurrency";
        DROP TABLE IF EXISTS "playbook_webhook";"""


MODELS_STATE = (
    "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"
    "Pr4KM45lfYDJ6gPIxOAjJgYfnlD4+l+ldGOO"
)
