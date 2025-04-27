from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ADD "execution_health_status" VARCHAR(255);
        ALTER TABLE "events_ingestion" ADD "enabled_health_status" VARCHAR(255);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" DROP COLUMN "execution_health_status";
        ALTER TABLE "events_ingestion" DROP COLUMN "enabled_health_status";"""
