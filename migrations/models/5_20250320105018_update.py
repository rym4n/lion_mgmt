from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" ADD "description" VARCHAR(255);
        ALTER TABLE "tool" RENAME COLUMN "tool_path" TO "path";
        ALTER TABLE "tool" RENAME COLUMN "tool_version" TO "version";
        ALTER TABLE "tool" RENAME COLUMN "tool_name" TO "name";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" RENAME COLUMN "version" TO "tool_version";
        ALTER TABLE "tool" RENAME COLUMN "name" TO "tool_name";
        ALTER TABLE "tool" RENAME COLUMN "path" TO "tool_path";
        ALTER TABLE "tool" DROP COLUMN "description";"""
