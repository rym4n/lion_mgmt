from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "event_related_script" ALTER COLUMN "script_id" TYPE JSONB USING "script_id"::JSONB;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "event_related_script" ALTER COLUMN "script_id" TYPE VARCHAR(255) USING "script_id"::VARCHAR(255);"""
