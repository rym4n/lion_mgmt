from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_events_c50db212";
        ALTER TABLE "runlog" ALTER COLUMN "event_id" SET NOT NULL;
        COMMENT ON COLUMN "runlog"."event_id" IS '事件id';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" ALTER COLUMN "event_id" DROP NOT NULL;
        COMMENT ON COLUMN "runlog"."event_id" IS '关联事件';
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_events_c50db212" FOREIGN KEY ("event_id") REFERENCES "events" ("id") ON DELETE CASCADE;"""
