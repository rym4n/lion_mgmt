from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "room_msg" ALTER COLUMN "type" TYPE VARCHAR(20) USING "type"::VARCHAR(20);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "room_msg" ALTER COLUMN "type" TYPE VARCHAR(10) USING "type"::VARCHAR(10);"""
