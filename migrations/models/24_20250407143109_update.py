from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources" ADD "tool_name" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources" DROP COLUMN "tool_id";
        ALTER TABLE "tool_resources_model" ADD "tool_name" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources_model" DROP COLUMN "tool_id";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources" ADD "tool_id" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources" DROP COLUMN "tool_name";
        ALTER TABLE "tool_resources_model" ADD "tool_id" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources_model" DROP COLUMN "tool_name";"""
