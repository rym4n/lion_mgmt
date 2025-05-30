from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" ADD "audit_node_id" VARCHAR(50) NOT NULL;
        ALTER TABLE "approvement" ADD "flow_id_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "approvement" ADD "created_at" TIMESTAMPTZ NOT NULL;
        ALTER TABLE "approvement" ADD "run_id" VARCHAR(50) NOT NULL;
        ALTER TABLE "approvement" DROP COLUMN "approvers";
        ALTER TABLE "approvement" ALTER COLUMN "processor_id" DROP NOT NULL;
        CREATE TABLE "approve_accounts" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE
);
        ALTER TABLE "approvement" ADD CONSTRAINT "fk_approvem_playbook_7068ac85" FOREIGN KEY ("flow_id_id") REFERENCES "playbook" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" DROP CONSTRAINT IF EXISTS "fk_approvem_playbook_7068ac85";
        DROP TABLE IF EXISTS "approve_accounts";
        ALTER TABLE "approvement" ADD "approvers" VARCHAR(255) NOT NULL;
        ALTER TABLE "approvement" DROP COLUMN "audit_node_id";
        ALTER TABLE "approvement" DROP COLUMN "flow_id_id";
        ALTER TABLE "approvement" DROP COLUMN "created_at";
        ALTER TABLE "approvement" DROP COLUMN "run_id";
        ALTER TABLE "approvement" ALTER COLUMN "processor_id" SET NOT NULL;"""
