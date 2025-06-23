from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "event_related_script" ADD "status" INT NOT NULL DEFAULT 0;
        ALTER TABLE "playbook_permissions" DROP COLUMN "users_id";
        ALTER TABLE "playbook_permissions" DROP COLUMN "roles_id";
        CREATE TABLE "pb_user" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_permissions_id" VARCHAR(36) NOT NULL REFERENCES "playbook_permissions" ("id") ON DELETE CASCADE
);
        CREATE TABLE "pb_role" (
    "playbook_permissions_id" VARCHAR(36) NOT NULL REFERENCES "playbook_permissions" ("id") ON DELETE CASCADE,
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "pb_user";
        DROP TABLE IF EXISTS "pb_role";
        ALTER TABLE "event_related_script" DROP COLUMN "status";
        ALTER TABLE "playbook_permissions" ADD "users_id" JSONB NOT NULL;
        ALTER TABLE "playbook_permissions" ADD "roles_id" JSONB NOT NULL;"""
