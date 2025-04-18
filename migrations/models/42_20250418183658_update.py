from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "room_accounts";
        ALTER TABLE "tool" ADD "internal" VARCHAR(255) NOT NULL;
        DROP TABLE IF EXISTS "room_msg";
        DROP TABLE IF EXISTS "room";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" DROP COLUMN "internal";
        CREATE TABLE "room_accounts" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "room_id" VARCHAR(36) NOT NULL REFERENCES "room" ("id") ON DELETE CASCADE
);"""
