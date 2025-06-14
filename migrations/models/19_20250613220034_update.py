from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" DROP CONSTRAINT IF EXISTS "fk_approvem_playbook_ac99dbe0";
        ALTER TABLE "approvement" ADD "next_node_id" VARCHAR(50) NOT NULL;
        ALTER TABLE "approvement" ADD "playbook_version_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "approvement" DROP COLUMN "workflow_id_id";
        ALTER TABLE "approvement" ADD CONSTRAINT "fk_approvem_playbook_27d2dbff" FOREIGN KEY ("playbook_version_id") REFERENCES "playbook_version" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" DROP CONSTRAINT IF EXISTS "fk_approvem_playbook_27d2dbff";
        ALTER TABLE "approvement" ADD "workflow_id_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "approvement" DROP COLUMN "next_node_id";
        ALTER TABLE "approvement" DROP COLUMN "playbook_version_id";
        ALTER TABLE "approvement" ADD CONSTRAINT "fk_approvem_playbook_ac99dbe0" FOREIGN KEY ("workflow_id_id") REFERENCES "playbook_version" ("id") ON DELETE CASCADE;"""
