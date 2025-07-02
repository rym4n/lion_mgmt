from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" ADD "agree_count" INT NOT NULL DEFAULT 0;
        ALTER TABLE "approve_processor" ADD "agree" BOOL NOT NULL;
        ALTER TABLE "approve_processor" DROP COLUMN "opinion";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" DROP COLUMN "agree_count";
        ALTER TABLE "approve_processor" ADD "opinion" INT NOT NULL;
        ALTER TABLE "approve_processor" DROP COLUMN "agree";"""
