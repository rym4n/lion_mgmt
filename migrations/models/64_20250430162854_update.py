from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "config" ALTER COLUMN "value" TYPE JSONB USING "value"::JSONB;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "config" ALTER COLUMN "value" TYPE TEXT USING "value"::TEXT;"""
