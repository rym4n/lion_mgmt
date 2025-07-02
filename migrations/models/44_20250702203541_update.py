from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ADD "creator_id" VARCHAR(36);
        ALTER TABLE "playbook" ADD CONSTRAINT "fk_playbook_account_48c30a25" FOREIGN KEY ("creator_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" DROP CONSTRAINT IF EXISTS "fk_playbook_account_48c30a25";
        ALTER TABLE "playbook" DROP COLUMN "creator_id";"""
