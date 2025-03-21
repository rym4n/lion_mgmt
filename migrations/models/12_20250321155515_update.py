from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources" ADD "creator" VARCHAR(255);
        ALTER TABLE "tool_resources" ADD "updater" VARCHAR(255);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources" DROP COLUMN "creator";
        ALTER TABLE "tool_resources" DROP COLUMN "updater";"""
