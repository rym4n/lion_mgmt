from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "approvement_account";
        ALTER TABLE "config" RENAME COLUMN "config_type" TO "type";
        ALTER TABLE "config" ALTER COLUMN "value" TYPE TEXT USING "value"::TEXT;
        CREATE TABLE "approve_accounts" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE
);
        CREATE TABLE "approve_processor" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "approve_processor";
        DROP TABLE IF EXISTS "approve_accounts";
        ALTER TABLE "config" RENAME COLUMN "type" TO "config_type";
        ALTER TABLE "config" ALTER COLUMN "value" TYPE JSONB USING "value"::JSONB;
        CREATE TABLE "approvement_account" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE
);"""
