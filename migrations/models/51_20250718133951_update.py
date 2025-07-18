from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ADD "scene_id" VARCHAR(36);
        ALTER TABLE "events" ADD CONSTRAINT "fk_events_scene_54486e55" FOREIGN KEY ("scene_id") REFERENCES "scene" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" DROP CONSTRAINT IF EXISTS "fk_events_scene_54486e55";
        ALTER TABLE "events" DROP COLUMN "scene_id";"""
