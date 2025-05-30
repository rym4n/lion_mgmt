from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" ADD "params" JSONB NOT NULL;
        ALTER TABLE "approvement" ALTER COLUMN "expire_time" SET NOT NULL;
        ALTER TABLE "approvement" ALTER COLUMN "approvers" SET NOT NULL;
        ALTER TABLE "approvement" ALTER COLUMN "processor_id" SET NOT NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" DROP COLUMN "params";
        ALTER TABLE "approvement" ALTER COLUMN "expire_time" DROP NOT NULL;
        ALTER TABLE "approvement" ALTER COLUMN "approvers" DROP NOT NULL;
        ALTER TABLE "approvement" ALTER COLUMN "processor_id" DROP NOT NULL;"""
