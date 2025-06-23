from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "pb_role";
        DROP TABLE IF EXISTS "pb_user";
        DROP TABLE IF EXISTS "playbook_permissions";
        CREATE TABLE "Playbook_user" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE
);
        CREATE TABLE "Playbook_role" (
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "Playbook_user";
        DROP TABLE IF EXISTS "Playbook_role";
        CREATE TABLE "pb_role" (
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE,
    "playbook_permissions_id" VARCHAR(36) NOT NULL REFERENCES "playbook_permissions" ("id") ON DELETE CASCADE
);
        CREATE TABLE "pb_user" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_permissions_id" VARCHAR(36) NOT NULL REFERENCES "playbook_permissions" ("id") ON DELETE CASCADE
);"""
