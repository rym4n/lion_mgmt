from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ALTER COLUMN "version" TYPE VARCHAR(30) USING "version"::VARCHAR(30);
        ALTER TABLE "playbook" DROP CONSTRAINT "playbook_name_key",ADD CONSTRAINT "playbook_name_key" UNIQUE ("name", "version");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ALTER COLUMN "version" TYPE VARCHAR(255) USING "version"::VARCHAR(255);"""
