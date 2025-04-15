from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ALTER COLUMN "syslog_config" TYPE VARCHAR(36) USING "syslog_config"::VARCHAR(36);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ALTER COLUMN "syslog_config" TYPE VARCHAR(255) USING "syslog_config"::VARCHAR(255);"""
