from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "permissions_menu" RENAME COLUMN "dp" TO "bp";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "permissions_menu" RENAME COLUMN "bp" TO "dp";"""
