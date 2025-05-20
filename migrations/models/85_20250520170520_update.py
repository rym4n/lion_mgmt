from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" DROP COLUMN "run_id";
        ALTER TABLE "runlog" ADD "event_id_id" VARCHAR(36);
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_events_69eabc33" FOREIGN KEY ("event_id_id") REFERENCES "events" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_events_69eabc33";
        ALTER TABLE "events" ADD "run_id" JSONB;
        ALTER TABLE "runlog" DROP COLUMN "event_id_id";"""
