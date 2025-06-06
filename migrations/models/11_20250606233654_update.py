from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_version" ALTER COLUMN "change_desc" DROP NOT NULL;
        ALTER TABLE "playbook_version" ALTER COLUMN "version" DROP DEFAULT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_version" ALTER COLUMN "change_desc" SET NOT NULL;
        ALTER TABLE "playbook_version" ALTER COLUMN "version" SET DEFAULT 0;"""
