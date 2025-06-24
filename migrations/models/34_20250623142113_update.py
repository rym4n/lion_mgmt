from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "action_permissions" RENAME COLUMN "roles_id" TO "roles";
        ALTER TABLE "action_permissions" RENAME COLUMN "users_id" TO "users";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "action_permissions" RENAME COLUMN "roles" TO "roles_id";
        ALTER TABLE "action_permissions" RENAME COLUMN "users" TO "users_id";"""
