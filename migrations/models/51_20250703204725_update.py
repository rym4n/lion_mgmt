from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" ADD "results" JSONB NOT NULL;
        ALTER TABLE "approvement" DROP COLUMN "process_records";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" ADD "process_records" JSONB NOT NULL;
        ALTER TABLE "approvement" DROP COLUMN "results";"""
