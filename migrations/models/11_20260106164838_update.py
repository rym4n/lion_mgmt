from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "tenant" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '0',
    "name" VARCHAR(255)
);
COMMENT ON COLUMN "tenant"."ctime" IS '创建时间';
COMMENT ON COLUMN "tenant"."utime" IS '更新时间';
COMMENT ON COLUMN "tenant"."deleted" IS '删除标识';
COMMENT ON COLUMN "tenant"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tenant"."name" IS '租户名称';
COMMENT ON TABLE "tenant" IS '角色权限';
        ALTER TABLE "account" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "action_permissions" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "approvement" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "config" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "event_related_script" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "events" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "events_ingestion" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "room_msg" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "room_msg_account" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "permissions_menu" RENAME TO "permissionsmenu";
        ALTER TABLE "permissionsmenu" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "playbook" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "playbook_permissions" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "playbook_version" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "playbook_webhook" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "role" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "room" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "runlog" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "scene" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "tag" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "timed" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "tool" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "tool_action" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "tool_resources" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "tool_resources_model" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        ALTER TABLE "variables" ADD "tenant_id" VARCHAR(255) DEFAULT '0';
        COMMENT ON COLUMN "account"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "action_permissions"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "approvement"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "config"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "event_related_script"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "events"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "events_ingestion"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "room_msg"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "room_msg_account"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "permissionsmenu"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "playbook"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "playbook_permissions"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "playbook_version"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "playbook_webhook"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "role"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "room"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "runlog"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "scene"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tag"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "timed"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tool"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tool_action"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tool_resources"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "tool_resources_model"."tenant_id" IS '租户ID';
COMMENT ON COLUMN "variables"."tenant_id" IS '租户ID';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tag" DROP COLUMN "tenant_id";
        ALTER TABLE "role" DROP COLUMN "tenant_id";
        ALTER TABLE "room" DROP COLUMN "tenant_id";
        ALTER TABLE "tool" DROP COLUMN "tenant_id";
        ALTER TABLE "scene" DROP COLUMN "tenant_id";
        ALTER TABLE "timed" DROP COLUMN "tenant_id";
        ALTER TABLE "config" DROP COLUMN "tenant_id";
        ALTER TABLE "events" DROP COLUMN "tenant_id";
        ALTER TABLE "runlog" DROP COLUMN "tenant_id";
        ALTER TABLE "account" DROP COLUMN "tenant_id";
        ALTER TABLE "room_msg" DROP COLUMN "tenant_id";
        ALTER TABLE "playbook" DROP COLUMN "tenant_id";
        ALTER TABLE "variables" DROP COLUMN "tenant_id";
        ALTER TABLE "tool_action" DROP COLUMN "tenant_id";
        ALTER TABLE "approvement" DROP COLUMN "tenant_id";
        ALTER TABLE "tool_resources" DROP COLUMN "tenant_id";
        ALTER TABLE "room_msg_account" DROP COLUMN "tenant_id";
        ALTER TABLE "events_ingestion" DROP COLUMN "tenant_id";
        ALTER TABLE "permissionsmenu" RENAME TO "permissions_menu";
        ALTER TABLE "permissionsmenu" DROP COLUMN "tenant_id";
        ALTER TABLE "playbook_version" DROP COLUMN "tenant_id";
        ALTER TABLE "playbook_webhook" DROP COLUMN "tenant_id";
        ALTER TABLE "action_permissions" DROP COLUMN "tenant_id";
        ALTER TABLE "event_related_script" DROP COLUMN "tenant_id";
        ALTER TABLE "tool_resources_model" DROP COLUMN "tenant_id";
        ALTER TABLE "playbook_permissions" DROP COLUMN "tenant_id";
        DROP TABLE IF EXISTS "tenant";"""


MODELS_STATE = (
    "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"
)
