from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ADD "internal" VARCHAR(255) NOT NULL DEFAULT False;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" DROP COLUMN "internal";"""
