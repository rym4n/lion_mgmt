from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ALTER COLUMN "creator_id" SET NOT NULL;
        ALTER TABLE "timed" DROP COLUMN "internal";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" ADD "internal" BOOL NOT NULL DEFAULT False;
        ALTER TABLE "playbook" ALTER COLUMN "creator_id" DROP NOT NULL;"""
