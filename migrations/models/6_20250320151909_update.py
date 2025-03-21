from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ALTER COLUMN "event_name" SET NOT NULL;
        ALTER TABLE "events" ALTER COLUMN "event_process_status" SET NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_events_event_n_0923f1" ON "events" ("event_name");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_events_event_n_0923f1";
        ALTER TABLE "events" ALTER COLUMN "event_name" DROP NOT NULL;
        ALTER TABLE "events" ALTER COLUMN "event_process_status" DROP NOT NULL;"""
