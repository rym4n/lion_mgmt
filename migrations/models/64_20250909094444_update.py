from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" ADD "execute_num" INT;
        ALTER TABLE "timed" ADD "success_num" INT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" DROP COLUMN "execute_num";
        ALTER TABLE "timed" DROP COLUMN "success_num";"""
