from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_action" ADD "users_id" JSONB;
        ALTER TABLE "tool_action" ADD "roles_id" JSONB;
        DROP TABLE IF EXISTS "action_permissions";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_action" DROP COLUMN "users_id";
        ALTER TABLE "tool_action" DROP COLUMN "roles_id";"""
