from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "room_msg_account" DROP CONSTRAINT IF EXISTS "fk_room_msg_account_7ce73890";
        ALTER TABLE "room_msg_account" ALTER COLUMN "account_id" TYPE VARCHAR(255) USING "account_id"::VARCHAR(255);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "room_msg_account" ALTER COLUMN "account_id" TYPE VARCHAR(36) USING "account_id"::VARCHAR(36);
        ALTER TABLE "room_msg_account" ADD CONSTRAINT "fk_room_msg_account_7ce73890" FOREIGN KEY ("account_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""
