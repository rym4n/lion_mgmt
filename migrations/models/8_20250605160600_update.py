from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" DROP CONSTRAINT IF EXISTS "fk_timed_playbook_e27b2709";
        ALTER TABLE "timed" RENAME COLUMN "playbook_version_id" TO "playbook_id";
        ALTER TABLE "timed" ADD CONSTRAINT "fk_timed_playbook_3093a793" FOREIGN KEY ("playbook_id") REFERENCES "playbook" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "timed" DROP CONSTRAINT IF EXISTS "fk_timed_playbook_3093a793";
        ALTER TABLE "timed" RENAME COLUMN "playbook_id" TO "playbook_version_id";
        ALTER TABLE "timed" ADD CONSTRAINT "fk_timed_playbook_e27b2709" FOREIGN KEY ("playbook_version_id") REFERENCES "playbook" ("id") ON DELETE CASCADE;"""
