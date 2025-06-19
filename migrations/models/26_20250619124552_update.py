from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "config" ALTER COLUMN "value" TYPE VARCHAR(1000) USING "value"::VARCHAR(1000);
        DROP TABLE IF EXISTS "playbook_version_permissions";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "config" ALTER COLUMN "value" TYPE TEXT USING "value"::TEXT;"""
