from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_permissions" DROP CONSTRAINT IF EXISTS "fk_playbook_playbook_d037ab53";
        ALTER TABLE "playbook_permissions" RENAME COLUMN "playbook_id" TO "pb_id";
        ALTER TABLE "playbook_permissions" ADD CONSTRAINT "fk_playbook_playbook_a6f47d25" FOREIGN KEY ("pb_id") REFERENCES "playbook" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_permissions" DROP CONSTRAINT IF EXISTS "fk_playbook_playbook_a6f47d25";
        ALTER TABLE "playbook_permissions" RENAME COLUMN "pb_id" TO "playbook_id";
        ALTER TABLE "playbook_permissions" ADD CONSTRAINT "fk_playbook_playbook_d037ab53" FOREIGN KEY ("playbook_id") REFERENCES "playbook" ("id") ON DELETE CASCADE;"""
