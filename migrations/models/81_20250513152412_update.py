from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "api_user_token" ALTER COLUMN "status" SET DEFAULT True;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "api_user_token" ALTER COLUMN "status" SET DEFAULT False;"""
