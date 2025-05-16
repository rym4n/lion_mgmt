from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" DROP COLUMN "event_level";
        ALTER TABLE "runlog" ADD "timer_id_id" VARCHAR(36);
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_timed_2b26d96a" FOREIGN KEY ("timer_id_id") REFERENCES "timed" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_timed_2b26d96a";
        ALTER TABLE "events" ADD "event_level" VARCHAR(50) NOT NULL;
        ALTER TABLE "runlog" DROP COLUMN "timer_id_id";"""
