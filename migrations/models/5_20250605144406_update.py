from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        COMMENT ON COLUMN "events"."event_process_status" IS '事件处理状态';
        CREATE TABLE "account_star_room" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "room_id" VARCHAR(36) NOT NULL REFERENCES "room" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "account_star_room";
        COMMENT ON COLUMN "events"."event_process_status" IS '事件处理状态（如 ASSIGNED, RESOLVED）';"""
