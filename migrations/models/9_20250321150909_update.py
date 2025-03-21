from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources" ALTER COLUMN "tool_id" TYPE VARCHAR(255) USING "tool_id"::VARCHAR(255);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources" ALTER COLUMN "tool_id" TYPE VARCHAR(36) USING "tool_id"::VARCHAR(36);"""
