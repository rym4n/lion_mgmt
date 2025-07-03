from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_permissions" ADD "permissions" JSONB;
        ALTER TABLE "playbook_permissions" DROP COLUMN "perms";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_permissions" ADD "perms" INT NOT NULL DEFAULT 0;
        ALTER TABLE "playbook_permissions" DROP COLUMN "permissions";"""
