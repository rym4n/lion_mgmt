from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ALTER COLUMN "internal" TYPE BOOL USING "internal"::BOOL;
        ALTER TABLE "tool" ALTER COLUMN "internal" SET DEFAULT False;
        ALTER TABLE "tool" ALTER COLUMN "internal" TYPE BOOL USING "internal"::BOOL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" ALTER COLUMN "internal" TYPE VARCHAR(255) USING "internal"::VARCHAR(255);
        ALTER TABLE "tool" ALTER COLUMN "internal" DROP DEFAULT;
        ALTER TABLE "playbook" ALTER COLUMN "internal" TYPE VARCHAR(255) USING "internal"::VARCHAR(255);"""
