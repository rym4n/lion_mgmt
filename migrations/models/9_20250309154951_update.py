from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "authentication_session" RENAME COLUMN "expire_time" TO "expiration_date";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "authentication_session" RENAME COLUMN "expiration_date" TO "expire_time";"""
