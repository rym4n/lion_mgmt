from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "audit" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "action" VARCHAR(255),
    "module" VARCHAR(255),
    "result" VARCHAR(255),
    "client_ip" VARCHAR(255),
    "client_ua" VARCHAR(255),
    "uri" VARCHAR(255),
    "user_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "audit"."ctime" IS '创建时间';
COMMENT ON COLUMN "audit"."utime" IS '更新时间';
COMMENT ON COLUMN "audit"."deleted" IS '删除标识';
COMMENT ON COLUMN "audit"."action" IS '操作';
COMMENT ON COLUMN "audit"."module" IS '模块';
COMMENT ON COLUMN "audit"."result" IS '结果';
COMMENT ON COLUMN "audit"."client_ip" IS '客户端IP';
COMMENT ON COLUMN "audit"."client_ua" IS '客户端UA';
COMMENT ON COLUMN "audit"."uri" IS '请求URL';
COMMENT ON TABLE "audit" IS '操作审计记录';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "audit";"""
