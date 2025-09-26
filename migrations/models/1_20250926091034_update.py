from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_version" ADD "flow_timeout" INT NOT NULL DEFAULT 300;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_version" DROP COLUMN "flow_timeout";"""
