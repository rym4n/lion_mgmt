from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" ADD "fail_num" INT;
        ALTER TABLE "timed" DROP COLUMN "success_num";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" ADD "success_num" INT;
        ALTER TABLE "timed" DROP COLUMN "fail_num";"""
