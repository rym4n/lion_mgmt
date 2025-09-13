from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" DROP COLUMN "fail_num";
        ALTER TABLE "timed" DROP COLUMN "execute_num";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" ADD "fail_num" INT;
        ALTER TABLE "timed" ADD "execute_num" INT;"""
