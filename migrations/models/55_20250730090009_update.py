from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "config" ALTER COLUMN "value" TYPE VARCHAR(4000) USING "value"::VARCHAR(4000);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "config" ALTER COLUMN "value" TYPE VARCHAR(1000) USING "value"::VARCHAR(1000);"""
