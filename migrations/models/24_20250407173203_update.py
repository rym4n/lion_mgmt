from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" ADD "hash_value" VARCHAR(255);
        ALTER TABLE "tool_action" ADD "roles_id" JSONB NOT NULL;
        ALTER TABLE "tool_action" ADD "users_id" JSONB NOT NULL;
        ALTER TABLE "tool_resources" ADD "tool_name" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources" DROP COLUMN "tool_id";
        ALTER TABLE "tool_resources_model" ADD "tool_name" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources_model" DROP COLUMN "tool_id";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" DROP COLUMN "hash_value";
        ALTER TABLE "tool_action" DROP COLUMN "roles_id";
        ALTER TABLE "tool_action" DROP COLUMN "users_id";
        ALTER TABLE "tool_resources" ADD "tool_id" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources" DROP COLUMN "tool_name";
        ALTER TABLE "tool_resources_model" ADD "tool_id" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources_model" DROP COLUMN "tool_name";"""
