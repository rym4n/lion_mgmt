from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "Playbook_role";
        DROP TABLE IF EXISTS "Playbook_user";
        CREATE TABLE "playbook_user" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE
);
        CREATE TABLE "playbook_role" (
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "playbook_user";
        DROP TABLE IF EXISTS "playbook_role";
        CREATE TABLE "Playbook_role" (
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE,
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE
);
        CREATE TABLE "Playbook_user" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE
);"""
