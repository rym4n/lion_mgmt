from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_account_76aa7d8e";
        ALTER TABLE "runlog" ADD "tool_version" VARCHAR(255);
        ALTER TABLE "runlog" RENAME COLUMN "creator_id" TO "executor_id";
        ALTER TABLE "runlog" DROP COLUMN "action_id";
        ALTER TABLE "runlog" ALTER COLUMN "tool_id" DROP NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "start_time" DROP NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "start_time" TYPE TIMESTAMPTZ USING "start_time"::TIMESTAMPTZ;
        ALTER TABLE "runlog" ALTER COLUMN "args" DROP DEFAULT;
        ALTER TABLE "runlog" ALTER COLUMN "args" DROP NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "tool_display_name" DROP NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "action_display_name" DROP NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "action_name" DROP NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "tool_name" DROP NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "result" DROP DEFAULT;
        ALTER TABLE "runlog" ALTER COLUMN "result" DROP NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "end_time" DROP NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "end_time" TYPE TIMESTAMPTZ USING "end_time"::TIMESTAMPTZ;
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_account_e6aa7233" FOREIGN KEY ("executor_id") REFERENCES "account" ("id") ON DELETE CASCADE;
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_runlog_run_id_466a30" ON "runlog" ("run_id", "node_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_runlog_run_id_466a30";
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_account_e6aa7233";
        ALTER TABLE "runlog" RENAME COLUMN "executor_id" TO "creator_id";
        ALTER TABLE "runlog" ADD "action_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "runlog" DROP COLUMN "tool_version";
        ALTER TABLE "runlog" ALTER COLUMN "tool_id" SET NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "start_time" SET NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "start_time" TYPE TIMESTAMPTZ USING "start_time"::TIMESTAMPTZ;
        ALTER TABLE "runlog" ALTER COLUMN "args" SET NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "args" SET;
        ALTER TABLE "runlog" ALTER COLUMN "tool_display_name" SET NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "action_display_name" SET NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "action_name" SET NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "tool_name" SET NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "result" SET NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "result" SET;
        ALTER TABLE "runlog" ALTER COLUMN "end_time" SET NOT NULL;
        ALTER TABLE "runlog" ALTER COLUMN "end_time" TYPE TIMESTAMPTZ USING "end_time"::TIMESTAMPTZ;
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_account_76aa7d8e" FOREIGN KEY ("creator_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""
