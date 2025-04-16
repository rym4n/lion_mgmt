from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ADD "version" INT NOT NULL DEFAULT 0;
        DROP TABLE IF EXISTS "permissions_menu";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" DROP COLUMN "version";"""
