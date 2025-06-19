from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "config" ALTER COLUMN "value" TYPE TEXT USING "value"::TEXT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "config" ALTER COLUMN "value" TYPE TEXT USING "value"::TEXT;"""
