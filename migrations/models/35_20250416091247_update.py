from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ALTER COLUMN "syslog_config" TYPE VARCHAR(255) USING "syslog_config"::VARCHAR(255);
        CREATE TABLE IF NOT EXISTS "permissions_menu" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "model" VARCHAR(255) NOT NULL UNIQUE,
    "description" VARCHAR(255),
    "Permissions" JSONB
);
COMMENT ON COLUMN "permissions_menu"."ctime" IS '创建时间';
COMMENT ON COLUMN "permissions_menu"."utime" IS '更新时间';
COMMENT ON COLUMN "permissions_menu"."deleted" IS '删除标识';
COMMENT ON TABLE "permissions_menu" IS '权限菜单';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ALTER COLUMN "syslog_config" TYPE VARCHAR(36) USING "syslog_config"::VARCHAR(36);
        DROP TABLE IF EXISTS "permissions_menu";"""
