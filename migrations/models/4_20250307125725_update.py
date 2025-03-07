from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" ALTER COLUMN "password" TYPE VARCHAR(255) USING "password"::VARCHAR(255);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" ALTER COLUMN "password" TYPE VARCHAR(32) USING "password"::VARCHAR(32);"""
