from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_events_event_n_0923f1";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_events_event_n_0923f1" ON "events" ("event_name");"""
