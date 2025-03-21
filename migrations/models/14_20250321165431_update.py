from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" DROP COLUMN "release_status";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ADD "release_status" INT;"""
