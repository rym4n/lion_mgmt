from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "license" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "data" TEXT NOT NULL
);
COMMENT ON COLUMN "license"."ctime" IS '创建时间';
COMMENT ON COLUMN "license"."utime" IS '更新时间';
COMMENT ON COLUMN "license"."deleted" IS '删除标识';
COMMENT ON COLUMN "license"."data" IS '许可数据';
COMMENT ON TABLE "license" IS '产品许可表';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "license";"""
