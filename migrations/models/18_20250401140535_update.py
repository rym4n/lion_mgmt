from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ADD "linked_execution" INT;
        COMMENT ON COLUMN "events_ingestion"."enabled_status" IS '是否开启上游接入';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" DROP COLUMN "linked_execution";
        COMMENT ON COLUMN "events_ingestion"."enabled_status" IS '启用状态';"""
