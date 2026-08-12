from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "inspection_category" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "name" VARCHAR(255) NOT NULL,
    "schedule_id" VARCHAR(36) NOT NULL,
    "app_name" VARCHAR(100) NOT NULL,
    "resource_ids" JSONB NOT NULL,
    "enabled" BOOL NOT NULL DEFAULT True
);
COMMENT ON COLUMN "inspection_category"."ctime" IS '创建时间';
COMMENT ON COLUMN "inspection_category"."utime" IS '更新时间';
COMMENT ON COLUMN "inspection_category"."deleted" IS '删除标识';
COMMENT ON COLUMN "inspection_category"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "inspection_category"."name" IS '类别名称';
COMMENT ON COLUMN "inspection_category"."schedule_id" IS '关联巡检任务ID';
COMMENT ON COLUMN "inspection_category"."app_name" IS '应用名称';
COMMENT ON COLUMN "inspection_category"."resource_ids" IS '资源ID列表(数组格式)';
COMMENT ON COLUMN "inspection_category"."enabled" IS '是否启用';
COMMENT ON TABLE "inspection_category" IS '巡检类别（中间层）';
        ALTER TABLE "inspection_schedule" ADD "owner" VARCHAR(255);
        COMMENT ON COLUMN "inspection_schedule"."schedule_cron" IS '定时周期(Cron表达式)';
        COMMENT ON COLUMN "inspection_schedule"."description" IS '任务描述';
        COMMENT ON COLUMN "inspection_schedule"."name" IS '任务名称';
        ALTER TABLE "inspection_target" ADD "category_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "inspection_target" DROP COLUMN "resource_config";
        ALTER TABLE "inspection_target" DROP COLUMN "category";
        ALTER TABLE "inspection_target" DROP COLUMN "app_name";
        COMMENT ON COLUMN "inspection_target"."description" IS '巡检项描述';
        COMMENT ON COLUMN "inspection_target"."name" IS '巡检项名称';
        COMMENT ON COLUMN "inspection_schedule"."owner" IS '负责人';
COMMENT ON COLUMN "inspection_target"."category_id" IS '关联巡检类别ID';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "inspection_target" ADD "resource_config" JSONB NOT NULL;
        ALTER TABLE "inspection_target" ADD "category" VARCHAR(100) NOT NULL;
        ALTER TABLE "inspection_target" ADD "app_name" VARCHAR(100) NOT NULL;
        ALTER TABLE "inspection_target" DROP COLUMN "category_id";
        COMMENT ON COLUMN "inspection_target"."description" IS '目标描述';
        COMMENT ON COLUMN "inspection_target"."name" IS '目标名称';
        ALTER TABLE "inspection_schedule" DROP COLUMN "owner";
        COMMENT ON COLUMN "inspection_schedule"."schedule_cron" IS 'Cron表达式';
        COMMENT ON COLUMN "inspection_schedule"."description" IS '配置描述';
        COMMENT ON COLUMN "inspection_schedule"."name" IS '配置名称';
        COMMENT ON COLUMN "inspection_target"."resource_config" IS '资源配置(JSON格式)';
COMMENT ON COLUMN "inspection_target"."category" IS '巡检分类';
COMMENT ON COLUMN "inspection_target"."app_name" IS '应用名称';
        DROP TABLE IF EXISTS "inspection_category";"""


MODELS_STATE = (
    "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"
    "Ezyr79PwE7EQA="
)
