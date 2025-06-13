from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "event_related_script" ALTER COLUMN "condition" TYPE TEXT USING "condition"::TEXT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "event_related_script" ALTER COLUMN "condition" TYPE VARCHAR(255) USING "condition"::VARCHAR(255);"""
