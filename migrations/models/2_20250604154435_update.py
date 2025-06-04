from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ADD "vector_config_raw" JSONB;
        COMMENT ON COLUMN "tool"."internal" IS '是否内置应用';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        COMMENT ON COLUMN "tool"."internal" IS '是否内部工具';
        ALTER TABLE "events_ingestion" DROP COLUMN "vector_config_raw";"""
