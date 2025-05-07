from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" ADD "run_id" JSONB;
        COMMENT ON COLUMN "playbook"."internal" IS '是否内置剧本';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events" DROP COLUMN "run_id";
        COMMENT ON COLUMN "playbook"."internal" IS '是否内部工具';"""
