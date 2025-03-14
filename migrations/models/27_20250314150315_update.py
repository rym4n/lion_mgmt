from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "variables" ADD "str_value" VARCHAR(255) NOT NULL;
        ALTER TABLE "variables" ADD "list_value" JSONB NOT NULL;
        ALTER TABLE "variables" DROP COLUMN "value";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "variables" ADD "value" VARCHAR(255) NOT NULL;
        ALTER TABLE "variables" DROP COLUMN "str_value";
        ALTER TABLE "variables" DROP COLUMN "list_value";"""
