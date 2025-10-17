from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" ADD "maintain" BOOL NOT NULL DEFAULT False;
        ALTER TABLE "tool" DROP COLUMN "download";
        COMMENT ON COLUMN "tool"."internal" IS '是否支持下载';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" ADD "download" BOOL NOT NULL DEFAULT False;
        ALTER TABLE "tool" DROP COLUMN "maintain";
        COMMENT ON COLUMN "tool"."internal" IS '是否内置应用';"""
