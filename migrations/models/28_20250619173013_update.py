from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_version_permissions" DROP CONSTRAINT IF EXISTS "fk_playbook_playbook_1e66f83d";
        ALTER TABLE "playbook_version_permissions" ADD "playbook_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "playbook_version_permissions" DROP COLUMN "pv_id";
        ALTER TABLE "playbook_version_permissions" ADD CONSTRAINT "fk_playbook_playbook_f3c9c902" FOREIGN KEY ("playbook_id") REFERENCES "playbook" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_version_permissions" DROP CONSTRAINT IF EXISTS "fk_playbook_playbook_f3c9c902";
        ALTER TABLE "playbook_version_permissions" ADD "pv_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "playbook_version_permissions" DROP COLUMN "playbook_id";
        ALTER TABLE "playbook_version_permissions" ADD CONSTRAINT "fk_playbook_playbook_1e66f83d" FOREIGN KEY ("pv_id") REFERENCES "playbook_version" ("id") ON DELETE CASCADE;"""
