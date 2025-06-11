from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" ADD "resource_name" VARCHAR(36);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP COLUMN "resource_name";"""
