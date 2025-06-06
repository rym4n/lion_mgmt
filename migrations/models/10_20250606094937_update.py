from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "room" ADD "run_id" JSONB;
        ALTER TABLE "room" ADD "updator_id" VARCHAR(36);
        ALTER TABLE "room" ADD CONSTRAINT "fk_room_account_aef63986" FOREIGN KEY ("updator_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "room" DROP CONSTRAINT IF EXISTS "fk_room_account_aef63986";
        ALTER TABLE "room" DROP COLUMN "run_id";
        ALTER TABLE "room" DROP COLUMN "updator_id";"""
