from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" ALTER COLUMN "playbook_id" DROP NOT NULL;
        ALTER TABLE "timed" ALTER COLUMN "action_id" DROP NOT NULL;
        ALTER TABLE "timed" ALTER COLUMN "success_rate" DROP NOT NULL;
        ALTER TABLE "timed" ALTER COLUMN "result" DROP NOT NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" ALTER COLUMN "playbook_id" SET NOT NULL;
        ALTER TABLE "timed" ALTER COLUMN "action_id" SET NOT NULL;
        ALTER TABLE "timed" ALTER COLUMN "success_rate" SET NOT NULL;
        ALTER TABLE "timed" ALTER COLUMN "result" SET NOT NULL;"""
