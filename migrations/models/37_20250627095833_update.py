from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "permissions_menu" ADD "dp" VARCHAR(32);
        ALTER TABLE "permissions_menu" ADD "url" JSONB;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "permissions_menu" DROP COLUMN "dp";
        ALTER TABLE "permissions_menu" DROP COLUMN "url";"""
