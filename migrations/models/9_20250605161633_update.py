from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" ADD "playbook_version" VARCHAR(50);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" DROP COLUMN "playbook_version";"""
