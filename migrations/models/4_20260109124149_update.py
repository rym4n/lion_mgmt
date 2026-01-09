from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "action_permissions" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "approvement" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "config" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "event_related_script" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "events" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "events_ingestion" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "room_msg" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "room_msg_account" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "permissions_menu" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "playbook" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "playbook_permissions" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "playbook_version" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "playbook_webhook" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "role" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "room" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "runlog" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "scene" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "tag" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "timed" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "tool" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "tool_action" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "tool_resources" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "tool_resources_model" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';
        ALTER TABLE "variables" ALTER COLUMN "tenant_id" SET DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tag" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "role" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "room" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "tool" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "scene" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "timed" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "config" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "events" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "runlog" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "account" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "room_msg" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "playbook" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "variables" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "tool_action" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "approvement" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "tool_resources" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "room_msg_account" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "events_ingestion" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "permissions_menu" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "playbook_version" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "playbook_webhook" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "action_permissions" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "event_related_script" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "tool_resources_model" ALTER COLUMN "tenant_id" SET DEFAULT '0';
        ALTER TABLE "playbook_permissions" ALTER COLUMN "tenant_id" SET DEFAULT '0';"""


MODELS_STATE = (
    "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"
    "YneL7+P/t8eb8="
)
