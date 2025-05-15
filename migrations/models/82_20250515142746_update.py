from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" ADD "creator" VARCHAR(32);
        ALTER TABLE "account" ADD "expiration" TIMESTAMPTZ;
        DROP TABLE IF EXISTS "api_user_token";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" DROP COLUMN "creator";
        ALTER TABLE "account" DROP COLUMN "expiration";"""
