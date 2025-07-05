from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "playbook_permissions_role";
        DROP TABLE IF EXISTS "playbook_permissions_user";
        ALTER TABLE "playbook_permissions" ADD "users_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "playbook_permissions" ADD "roles_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "playbook_permissions" ADD CONSTRAINT "fk_playbook_role_1245ee4e" FOREIGN KEY ("roles_id") REFERENCES "role" ("id") ON DELETE CASCADE;
        ALTER TABLE "playbook_permissions" ADD CONSTRAINT "fk_playbook_account_f271118a" FOREIGN KEY ("users_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_permissions" DROP CONSTRAINT IF EXISTS "fk_playbook_account_f271118a";
        ALTER TABLE "playbook_permissions" DROP CONSTRAINT IF EXISTS "fk_playbook_role_1245ee4e";
        ALTER TABLE "playbook_permissions" DROP COLUMN "users_id";
        ALTER TABLE "playbook_permissions" DROP COLUMN "roles_id";
        CREATE TABLE "playbook_permissions_role" (
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE,
    "playbook_permissions_id" VARCHAR(36) NOT NULL REFERENCES "playbook_permissions" ("id") ON DELETE CASCADE
);
        CREATE TABLE "playbook_permissions_user" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_permissions_id" VARCHAR(36) NOT NULL REFERENCES "playbook_permissions" ("id") ON DELETE CASCADE
);"""
