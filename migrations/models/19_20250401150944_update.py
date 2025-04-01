from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ADD "syslog_port" VARCHAR(36) NOT NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" DROP COLUMN "syslog_port";"""
