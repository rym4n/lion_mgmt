from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ALTER COLUMN "created_id" TYPE VARCHAR(36) USING "created_id"::VARCHAR(36);
        ALTER TABLE "events" ALTER COLUMN "event_level" TYPE VARCHAR(50) USING "event_level"::VARCHAR(50);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ALTER COLUMN "created_id" TYPE INT USING "created_id"::INT;
        ALTER TABLE "events" ALTER COLUMN "event_level" TYPE INT USING "event_level"::INT;"""
