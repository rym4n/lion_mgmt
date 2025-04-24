from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "config" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "key" VARCHAR(255) NOT NULL UNIQUE,
    "value" TEXT NOT NULL,
    "config_type" VARCHAR(255) NOT NULL,
    "module" VARCHAR(255) NOT NULL
);
COMMENT ON COLUMN "config"."ctime" IS '创建时间';
COMMENT ON COLUMN "config"."utime" IS '更新时间';
COMMENT ON COLUMN "config"."deleted" IS '删除标识';
COMMENT ON COLUMN "config"."key" IS '配置项';
COMMENT ON COLUMN "config"."value" IS '配置值';
COMMENT ON COLUMN "config"."config_type" IS '配置状态';
COMMENT ON COLUMN "config"."module" IS '所属模块';
COMMENT ON TABLE "config" IS '通用配置表';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "config";"""
