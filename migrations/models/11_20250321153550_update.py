from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources" ADD "description" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources" ADD "name" VARCHAR(255) NOT NULL;
        ALTER TABLE "tool_resources" ADD "product_name" VARCHAR(255);
        ALTER TABLE "tool_resources" ADD "product_supplier" VARCHAR(255);
        ALTER TABLE "tool_resources" DROP COLUMN "updater";
        ALTER TABLE "tool_resources" DROP COLUMN "template";
        ALTER TABLE "tool_resources" DROP COLUMN "creator";
        ALTER TABLE "tool_resources" ALTER COLUMN "tool_id" SET NOT NULL;
        ALTER TABLE "tool_resources" ALTER COLUMN "resources" SET NOT NULL;
        COMMENT ON COLUMN "tool_resources"."resources" IS '资源配置';
        CREATE TABLE IF NOT EXISTS "tool_resources_model" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tool_id" VARCHAR(255) NOT NULL,
    "resources" JSONB NOT NULL
);
COMMENT ON COLUMN "tool_resources"."description" IS '资源描述';
COMMENT ON COLUMN "tool_resources"."name" IS '资源名称';
COMMENT ON COLUMN "tool_resources"."product_name" IS '产品名称';
COMMENT ON COLUMN "tool_resources"."product_supplier" IS '供应商';
COMMENT ON COLUMN "tool_resources_model"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_resources_model"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_resources_model"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool_resources_model"."tool_id" IS '工具id';
COMMENT ON COLUMN "tool_resources_model"."resources" IS '资源模板';
COMMENT ON TABLE "tool_resources_model" IS '工具资源模板表';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_resources" ADD "updater" VARCHAR(255);
        ALTER TABLE "tool_resources" ADD "template" BOOL NOT NULL DEFAULT False;
        ALTER TABLE "tool_resources" ADD "creator" VARCHAR(255);
        ALTER TABLE "tool_resources" DROP COLUMN "description";
        ALTER TABLE "tool_resources" DROP COLUMN "name";
        ALTER TABLE "tool_resources" DROP COLUMN "product_name";
        ALTER TABLE "tool_resources" DROP COLUMN "product_supplier";
        ALTER TABLE "tool_resources" ALTER COLUMN "tool_id" DROP NOT NULL;
        ALTER TABLE "tool_resources" ALTER COLUMN "resources" DROP NOT NULL;
        COMMENT ON COLUMN "tool_resources"."resources" IS '资源详情';
        DROP TABLE IF EXISTS "tool_resources_model";"""
