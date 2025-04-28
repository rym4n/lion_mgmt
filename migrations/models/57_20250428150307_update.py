from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ALTER COLUMN "version" TYPE VARCHAR(255) USING "version"::VARCHAR(255);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ALTER COLUMN "version" TYPE INT USING "version"::INT;"""
