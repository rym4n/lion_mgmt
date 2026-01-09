from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" DROP COLUMN "tenant_id";
        ALTER TABLE "action_permissions" DROP COLUMN "tenant_id";
        ALTER TABLE "approvement" DROP COLUMN "tenant_id";
        ALTER TABLE "config" DROP COLUMN "tenant_id";
        ALTER TABLE "event_related_script" DROP COLUMN "tenant_id";
        ALTER TABLE "events" DROP COLUMN "tenant_id";
        ALTER TABLE "events_ingestion" DROP COLUMN "tenant_id";
        ALTER TABLE "room_msg" DROP COLUMN "tenant_id";
        ALTER TABLE "room_msg_account" DROP COLUMN "tenant_id";
        ALTER TABLE "permissions_menu" DROP COLUMN "tenant_id";
        ALTER TABLE "playbook" DROP COLUMN "tenant_id";
        ALTER TABLE "playbook_permissions" DROP COLUMN "tenant_id";
        ALTER TABLE "playbook_version" DROP COLUMN "tenant_id";
        ALTER TABLE "playbook_webhook" DROP COLUMN "tenant_id";
        ALTER TABLE "role" DROP COLUMN "tenant_id";
        ALTER TABLE "room" DROP COLUMN "tenant_id";
        ALTER TABLE "runlog" DROP COLUMN "tenant_id";
        ALTER TABLE "scene" DROP COLUMN "tenant_id";
        ALTER TABLE "tag" DROP COLUMN "tenant_id";
        ALTER TABLE "timed" DROP COLUMN "tenant_id";
        ALTER TABLE "tool" DROP COLUMN "tenant_id";
        ALTER TABLE "tool_action" DROP COLUMN "tenant_id";
        ALTER TABLE "tool_resources" DROP COLUMN "tenant_id";
        ALTER TABLE "tool_resources_model" DROP COLUMN "tenant_id";
        ALTER TABLE "variables" DROP COLUMN "tenant_id";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tag" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "role" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "room" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "tool" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "scene" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "timed" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "config" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "events" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "runlog" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "account" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "room_msg" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "playbook" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "variables" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "tool_action" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "approvement" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "tool_resources" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "room_msg_account" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "events_ingestion" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "permissions_menu" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "playbook_version" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "playbook_webhook" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "action_permissions" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "event_related_script" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "tool_resources_model" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "playbook_permissions" ADD "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
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
COMMENT ON COLUMN "playbook_permissions"."tenant_id" IS '租户ID';"""


MODELS_STATE = (
    "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"
    "n0WJ0acLZTAxRd+Ih04cO7Gb79F0D+Bu0="
)
