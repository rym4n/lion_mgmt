from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "events_ingestion" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "event_ingestion_name" VARCHAR(255),
    "event_ingestion_describe" VARCHAR(255),
    "event_source_type" VARCHAR(255),
    "transforms_type" VARCHAR(255),
    "enabled_status" INT,
    "release_status" INT,
    "created_id" VARCHAR(36) NOT NULL,
    "file_path" VARCHAR(255)
);
COMMENT ON COLUMN "events_ingestion"."ctime" IS '创建时间';
COMMENT ON COLUMN "events_ingestion"."utime" IS '更新时间';
COMMENT ON COLUMN "events_ingestion"."deleted" IS '删除标识';
COMMENT ON COLUMN "events_ingestion"."event_ingestion_name" IS '事件接入名称';
COMMENT ON COLUMN "events_ingestion"."event_ingestion_describe" IS '事件接入描述';
COMMENT ON COLUMN "events_ingestion"."event_source_type" IS '事件来源类型';
COMMENT ON COLUMN "events_ingestion"."transforms_type" IS '转换类型';
COMMENT ON COLUMN "events_ingestion"."enabled_status" IS '启用状态';
COMMENT ON COLUMN "events_ingestion"."release_status" IS '发布状态';
COMMENT ON COLUMN "events_ingestion"."created_id" IS '创建人ID，关联用户表';
COMMENT ON COLUMN "events_ingestion"."file_path" IS '配置文件路径';
COMMENT ON TABLE "events_ingestion" IS '事件接入列表';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "events_ingestion";"""
