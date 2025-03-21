from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" ADD "creator" VARCHAR(255);
        ALTER TABLE "tool" ADD "updater" VARCHAR(255);
        CREATE TABLE IF NOT EXISTS "tool_resources" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tool_id" VARCHAR(36),
    "resources" JSONB,
    "creator" VARCHAR(255),
    "updater" VARCHAR(255)
);
COMMENT ON COLUMN "tool"."creator" IS '创建者';
COMMENT ON COLUMN "tool"."updater" IS '更新者';
COMMENT ON COLUMN "tool_resources"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_resources"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_resources"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool_resources"."tool_id" IS '工具id';
COMMENT ON COLUMN "tool_resources"."resources" IS '资源详情';
COMMENT ON COLUMN "tool_resources"."creator" IS '创建者';
COMMENT ON COLUMN "tool_resources"."updater" IS '更新者';
COMMENT ON TABLE "tool_resources" IS '工具资源管理表';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool" DROP COLUMN "creator";
        ALTER TABLE "tool" DROP COLUMN "updater";
        DROP TABLE IF EXISTS "tool_resources";"""
