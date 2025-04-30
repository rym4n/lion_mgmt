from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" ADD "action_input" JSONB;
        ALTER TABLE "timed" ADD "playbook_input" JSONB;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" DROP COLUMN "action_input";
        ALTER TABLE "timed" DROP COLUMN "playbook_input";"""
