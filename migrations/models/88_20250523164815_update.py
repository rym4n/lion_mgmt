from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "system_status" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "systemname" VARCHAR(255),
    "instancename" VARCHAR(255),
    "uptime" VARCHAR(255),
    "checkTime" VARCHAR(255),
    "status" VARCHAR(255)
);
COMMENT ON COLUMN "system_status"."ctime" IS '创建时间';
COMMENT ON COLUMN "system_status"."utime" IS '更新时间';
COMMENT ON COLUMN "system_status"."deleted" IS '删除标识';
COMMENT ON COLUMN "system_status"."systemname" IS '系统名';
COMMENT ON COLUMN "system_status"."instancename" IS '实例名';
COMMENT ON COLUMN "system_status"."uptime" IS '启动时间';
COMMENT ON COLUMN "system_status"."checkTime" IS '检查时间';
COMMENT ON COLUMN "system_status"."status" IS '状态';
COMMENT ON TABLE "system_status" IS '系统状态表';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "system_status";"""
