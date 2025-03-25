from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" ADD "mfa_bind" BOOL NOT NULL DEFAULT False;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" DROP COLUMN "mfa_bind";"""
