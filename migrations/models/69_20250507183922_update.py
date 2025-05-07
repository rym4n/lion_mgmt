from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_account_e6aa7233";
        ALTER TABLE "events" DROP COLUMN "run_id";
        ALTER TABLE "runlog" ADD "executor" VARCHAR(36);
        ALTER TABLE "runlog" DROP COLUMN "tool_id";
        ALTER TABLE "runlog" DROP COLUMN "executor_id";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ADD "run_id" JSONB;
        ALTER TABLE "runlog" ADD "tool_id" VARCHAR(36);
        ALTER TABLE "runlog" ADD "executor_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "runlog" DROP COLUMN "executor";
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_account_e6aa7233" FOREIGN KEY ("executor_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""
