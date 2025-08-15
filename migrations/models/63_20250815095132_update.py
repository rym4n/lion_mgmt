from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" DROP COLUMN "action_resource";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" ADD "action_resource" JSONB;"""
