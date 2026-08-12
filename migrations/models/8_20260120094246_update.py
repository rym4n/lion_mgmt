from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" DROP CONSTRAINT IF EXISTS "events_ingestion_syslog_config_key";
        DROP INDEX IF EXISTS "uid_events_inge_syslog__8aa9f5";
        COMMENT ON COLUMN "account"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "action_permissions"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "approvement"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "config"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "event_related_script"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "events"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "events_ingestion"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "room_msg"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "room_msg_account"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "permissions_menu"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "playbook"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "playbook_permissions"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "playbook_version"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "playbook_webhook"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "role"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "room"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "runlog"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "scene"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "tag"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "timed"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "tool"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "tool_action"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "tool_resources"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "tool_resources_model"."tenant_id" IS '默认租户ID';
        COMMENT ON COLUMN "variables"."tenant_id" IS '默认租户ID';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        COMMENT ON COLUMN "tag"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "role"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "room"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "tool"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "scene"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "timed"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "config"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "events"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "runlog"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "account"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "room_msg"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "playbook"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "variables"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "tool_action"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "approvement"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "tool_resources"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "room_msg_account"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "events_ingestion"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "permissions_menu"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "playbook_version"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "playbook_webhook"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "action_permissions"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "event_related_script"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "tool_resources_model"."tenant_id" IS '租户ID';
        COMMENT ON COLUMN "playbook_permissions"."tenant_id" IS '租户ID';
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_events_inge_syslog__8aa9f5" ON "events_ingestion" ("syslog_config");"""


MODELS_STATE = (
    "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"
    "sH7c"
)
