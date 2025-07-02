from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_permissions" ADD "perms" INT NOT NULL DEFAULT 0;
        ALTER TABLE "playbook_permissions" DROP COLUMN "users";
        ALTER TABLE "playbook_permissions" DROP COLUMN "roles";
        CREATE TABLE "playbook_permissions_user" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_permissions_id" VARCHAR(36) NOT NULL REFERENCES "playbook_permissions" ("id") ON DELETE CASCADE
);
        CREATE TABLE "playbook_permissions_role" (
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE,
    "playbook_permissions_id" VARCHAR(36) NOT NULL REFERENCES "playbook_permissions" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "playbook_permissions_user";
        DROP TABLE IF EXISTS "playbook_permissions_role";
        ALTER TABLE "playbook_permissions" ADD "users" JSONB NOT NULL;
        ALTER TABLE "playbook_permissions" ADD "roles" JSONB NOT NULL;
        ALTER TABLE "playbook_permissions" DROP COLUMN "perms";"""
