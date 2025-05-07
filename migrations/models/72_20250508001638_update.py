from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" ALTER COLUMN "result" TYPE TEXT USING "result"::TEXT;
        COMMENT ON COLUMN "runlog"."result" IS '输出结果,啥格式都有可能';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" ALTER COLUMN "result" TYPE JSONB USING "result"::JSONB;
        COMMENT ON COLUMN "runlog"."result" IS '输出结果';"""
