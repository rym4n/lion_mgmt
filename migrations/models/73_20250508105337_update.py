from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ADD "room_id" VARCHAR(36);
        ALTER TABLE "events" ADD CONSTRAINT "fk_events_room_462fd8ed" FOREIGN KEY ("room_id") REFERENCES "room" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" DROP CONSTRAINT IF EXISTS "fk_events_room_462fd8ed";
        ALTER TABLE "events" DROP COLUMN "room_id";"""
