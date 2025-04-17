from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_playbook_name_207539";
        ALTER TABLE "events_ingestion" ADD "vector_name" VARCHAR(255);
        ALTER TABLE "events_ingestion" ADD "creator_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "events_ingestion" ADD "updator_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "events_ingestion" DROP COLUMN "created_id";
        ALTER TABLE "events_ingestion" ADD CONSTRAINT "fk_events_i_account_194fb3be" FOREIGN KEY ("updator_id") REFERENCES "account" ("id") ON DELETE CASCADE;
        ALTER TABLE "events_ingestion" ADD CONSTRAINT "fk_events_i_account_63fee39a" FOREIGN KEY ("creator_id") REFERENCES "account" ("id") ON DELETE CASCADE;
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_playbook_name_7d99d8" ON "playbook" ("name", "version");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" DROP CONSTRAINT IF EXISTS "fk_events_i_account_63fee39a";
        ALTER TABLE "events_ingestion" DROP CONSTRAINT IF EXISTS "fk_events_i_account_194fb3be";
        DROP INDEX IF EXISTS "uid_playbook_name_7d99d8";
        ALTER TABLE "events_ingestion" ADD "created_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "events_ingestion" DROP COLUMN "vector_name";
        ALTER TABLE "events_ingestion" DROP COLUMN "creator_id";
        ALTER TABLE "events_ingestion" DROP COLUMN "updator_id";
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_playbook_name_207539" ON "playbook" ("name");"""
