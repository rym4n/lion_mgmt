from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources" ADD "engine" VARCHAR(255);
        ALTER TABLE "tool_resources" DROP COLUMN "product_supplier";
        ALTER TABLE "tool_resources" DROP COLUMN "product_name";
        ALTER TABLE "tool_resources" DROP COLUMN "description";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources" ADD "product_supplier" VARCHAR(255);
        ALTER TABLE "tool_resources" ADD "product_name" VARCHAR(255);
        ALTER TABLE "tool_resources" ADD "description" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources" DROP COLUMN "engine";"""
