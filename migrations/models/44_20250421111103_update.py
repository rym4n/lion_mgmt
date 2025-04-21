from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" ALTER COLUMN "tag" TYPE JSONB USING "tag"::JSONB;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" ALTER COLUMN "tag" TYPE VARCHAR(255) USING "tag"::VARCHAR(255);"""
