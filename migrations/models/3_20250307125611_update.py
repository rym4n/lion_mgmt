from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" ALTER COLUMN "email" TYPE VARCHAR(150) USING "email"::VARCHAR(150);
        DROP TABLE IF EXISTS "two_step_session";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" ALTER COLUMN "email" TYPE VARCHAR(255) USING "email"::VARCHAR(255);"""
