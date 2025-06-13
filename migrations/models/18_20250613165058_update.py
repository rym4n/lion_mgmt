from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" ADD "playbook_name" VARCHAR(36);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP COLUMN "playbook_name";"""
