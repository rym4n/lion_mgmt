from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "audit_log" ADD "request_data" JSONB;
        CREATE TABLE IF NOT EXISTS "elements" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "ele_value" VARCHAR(255) NOT NULL,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "audit_log"."request_data" IS '请求数据';
COMMENT ON COLUMN "elements"."ctime" IS '创建时间';
COMMENT ON COLUMN "elements"."utime" IS '更新时间';
COMMENT ON COLUMN "elements"."deleted" IS '删除标识';
COMMENT ON COLUMN "elements"."ele_value" IS '元素值';
COMMENT ON COLUMN "elements"."updator_id" IS '更新人';
COMMENT ON TABLE "elements" IS '全局变量一般是个列表，可以有多个元素';
        ALTER TABLE "variables" ADD "creator_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "variables" DROP COLUMN "list_value";
        ALTER TABLE "variables" DROP COLUMN "str_value";
        COMMENT ON COLUMN "variables"."type" IS '变量类型，枚举型： list, set, ipv4, ipv6';
        ALTER TABLE "variables" ADD CONSTRAINT "fk_variable_account_7d8a5f50" FOREIGN KEY ("creator_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "variables" DROP CONSTRAINT IF EXISTS "fk_variable_account_7d8a5f50";
        ALTER TABLE "audit_log" DROP COLUMN "request_data";
        ALTER TABLE "variables" ADD "list_value" JSONB NOT NULL;
        ALTER TABLE "variables" ADD "str_value" VARCHAR(255) NOT NULL;
        ALTER TABLE "variables" DROP COLUMN "creator_id";
        COMMENT ON COLUMN "variables"."type" IS '变量类型';
        DROP TABLE IF EXISTS "elements";"""
