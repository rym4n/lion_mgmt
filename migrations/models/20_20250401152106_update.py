from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" RENAME COLUMN "syslog_port" TO "syslog_config";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" RENAME COLUMN "syslog_config" TO "syslog_port";"""
