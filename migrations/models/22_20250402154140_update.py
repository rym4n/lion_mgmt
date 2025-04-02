from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "event_related_script" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "condition" VARCHAR(255),
    "event_ingestion_id" VARCHAR(255),
    "topic" VARCHAR(255),
    "script_id" VARCHAR(255)
);
COMMENT ON COLUMN "event_related_script"."ctime" IS '创建时间';
COMMENT ON COLUMN "event_related_script"."utime" IS '更新时间';
COMMENT ON COLUMN "event_related_script"."deleted" IS '删除标识';
COMMENT ON COLUMN "event_related_script"."condition" IS '执行条件';
COMMENT ON COLUMN "event_related_script"."event_ingestion_id" IS '事件接入id';
COMMENT ON COLUMN "event_related_script"."topic" IS '主题名';
COMMENT ON COLUMN "event_related_script"."script_id" IS '剧本id';
COMMENT ON TABLE "event_related_script" IS '事件关联剧本表';
        ALTER TABLE "events_ingestion" DROP COLUMN "script_id";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "events_ingestion" ADD "script_id" JSONB;
        DROP TABLE IF EXISTS "event_related_script";"""
