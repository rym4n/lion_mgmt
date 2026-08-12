from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "inspection_app" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "name" VARCHAR(100) NOT NULL UNIQUE,
    "display_name" VARCHAR(255) NOT NULL,
    "description" TEXT,
    "resource_schema" JSONB NOT NULL,
    "available_actions" JSONB NOT NULL,
    "enabled" BOOL NOT NULL DEFAULT True
);
COMMENT ON COLUMN "inspection_app"."ctime" IS '创建时间';
COMMENT ON COLUMN "inspection_app"."utime" IS '更新时间';
COMMENT ON COLUMN "inspection_app"."deleted" IS '删除标识';
COMMENT ON COLUMN "inspection_app"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "inspection_app"."name" IS '应用名称';
COMMENT ON COLUMN "inspection_app"."display_name" IS '显示名称';
COMMENT ON COLUMN "inspection_app"."description" IS '应用描述';
COMMENT ON COLUMN "inspection_app"."resource_schema" IS '资源配置Schema(JSON Schema格式)';
COMMENT ON COLUMN "inspection_app"."available_actions" IS '可用动作列表';
COMMENT ON COLUMN "inspection_app"."enabled" IS '是否启用';
COMMENT ON TABLE "inspection_app" IS '巡检应用信息';
        CREATE TABLE IF NOT EXISTS "inspection_result" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "target_id" VARCHAR(36) NOT NULL,
    "start_time" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "end_time" TIMESTAMPTZ,
    "status" VARCHAR(20) NOT NULL,
    "result_data" JSONB,
    "error_message" TEXT,
    "duration" INT
);
CREATE INDEX IF NOT EXISTS "idx_inspection__target__d14276" ON "inspection_result" ("target_id", "start_time");
COMMENT ON COLUMN "inspection_result"."ctime" IS '创建时间';
COMMENT ON COLUMN "inspection_result"."utime" IS '更新时间';
COMMENT ON COLUMN "inspection_result"."deleted" IS '删除标识';
COMMENT ON COLUMN "inspection_result"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "inspection_result"."target_id" IS '巡检目标ID';
COMMENT ON COLUMN "inspection_result"."start_time" IS '开始时间';
COMMENT ON COLUMN "inspection_result"."end_time" IS '结束时间';
COMMENT ON COLUMN "inspection_result"."status" IS '执行状态: success/failed/timeout';
COMMENT ON COLUMN "inspection_result"."result_data" IS '巡检结果数据';
COMMENT ON COLUMN "inspection_result"."error_message" IS '错误信息';
COMMENT ON COLUMN "inspection_result"."duration" IS '执行耗时(秒)';
COMMENT ON TABLE "inspection_result" IS '巡检结果记录';
        CREATE TABLE IF NOT EXISTS "inspection_schedule" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "name" VARCHAR(255) NOT NULL DEFAULT '默认巡检计划',
    "schedule_cron" VARCHAR(100) NOT NULL,
    "enabled" BOOL NOT NULL DEFAULT True,
    "description" TEXT
);
COMMENT ON COLUMN "inspection_schedule"."ctime" IS '创建时间';
COMMENT ON COLUMN "inspection_schedule"."utime" IS '更新时间';
COMMENT ON COLUMN "inspection_schedule"."deleted" IS '删除标识';
COMMENT ON COLUMN "inspection_schedule"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "inspection_schedule"."name" IS '配置名称';
COMMENT ON COLUMN "inspection_schedule"."schedule_cron" IS 'Cron表达式';
COMMENT ON COLUMN "inspection_schedule"."enabled" IS '是否启用';
COMMENT ON COLUMN "inspection_schedule"."description" IS '配置描述';
COMMENT ON TABLE "inspection_schedule" IS '巡检调度配置（每个租户一个配置）';
        CREATE TABLE IF NOT EXISTS "inspection_target" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "name" VARCHAR(255) NOT NULL,
    "content" TEXT NOT NULL,
    "category" VARCHAR(100) NOT NULL,
    "standard" TEXT NOT NULL,
    "action_name" VARCHAR(100) NOT NULL,
    "app_name" VARCHAR(100) NOT NULL,
    "resource_config" JSONB NOT NULL,
    "enabled" BOOL NOT NULL DEFAULT True,
    "description" TEXT
);
COMMENT ON COLUMN "inspection_target"."ctime" IS '创建时间';
COMMENT ON COLUMN "inspection_target"."utime" IS '更新时间';
COMMENT ON COLUMN "inspection_target"."deleted" IS '删除标识';
COMMENT ON COLUMN "inspection_target"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "inspection_target"."name" IS '目标名称';
COMMENT ON COLUMN "inspection_target"."content" IS '巡检内容';
COMMENT ON COLUMN "inspection_target"."category" IS '巡检分类';
COMMENT ON COLUMN "inspection_target"."standard" IS '巡检标准';
COMMENT ON COLUMN "inspection_target"."action_name" IS '巡检动作名称';
COMMENT ON COLUMN "inspection_target"."app_name" IS '应用名称';
COMMENT ON COLUMN "inspection_target"."resource_config" IS '资源配置(JSON格式)';
COMMENT ON COLUMN "inspection_target"."enabled" IS '是否启用';
COMMENT ON COLUMN "inspection_target"."description" IS '目标描述';
COMMENT ON TABLE "inspection_target" IS '巡检目标配置';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "inspection_app";
        DROP TABLE IF EXISTS "inspection_result";
        DROP TABLE IF EXISTS "inspection_schedule";
        DROP TABLE IF EXISTS "inspection_target";"""


MODELS_STATE = (
    "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"
)
