from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ALTER COLUMN "creator_id" DROP NOT NULL;
        ALTER TABLE "events" ALTER COLUMN "updator_id" DROP NOT NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ALTER COLUMN "creator_id" SET NOT NULL;
        ALTER TABLE "events" ALTER COLUMN "updator_id" SET NOT NULL;"""
