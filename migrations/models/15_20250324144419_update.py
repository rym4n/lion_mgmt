from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ALTER COLUMN "owner" DROP NOT NULL;
        ALTER TABLE "events_ingestion" ADD "script_id" JSONB;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ALTER COLUMN "owner" SET NOT NULL;
        ALTER TABLE "events_ingestion" DROP COLUMN "script_id";"""
