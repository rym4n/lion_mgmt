from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ADD "creator_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "events" ADD "updator_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "events" DROP COLUMN "created_id";
        ALTER TABLE "events" ADD CONSTRAINT "fk_events_account_bf5157a9" FOREIGN KEY ("updator_id") REFERENCES "account" ("id") ON DELETE CASCADE;
        ALTER TABLE "events" ADD CONSTRAINT "fk_events_account_8a8c2517" FOREIGN KEY ("creator_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" DROP CONSTRAINT IF EXISTS "fk_events_account_8a8c2517";
        ALTER TABLE "events" DROP CONSTRAINT IF EXISTS "fk_events_account_bf5157a9";
        ALTER TABLE "events" ADD "created_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "events" DROP COLUMN "creator_id";
        ALTER TABLE "events" DROP COLUMN "updator_id";"""
