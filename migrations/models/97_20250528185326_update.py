from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ALTER COLUMN "vector_config" TYPE JSONB USING "vector_config"::JSONB;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ALTER COLUMN "vector_config" TYPE TEXT USING "vector_config"::TEXT;"""
