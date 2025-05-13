from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "api_user_token" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(32) NOT NULL UNIQUE,
    "token" TEXT NOT NULL,
    "description" VARCHAR(255),
    "status" BOOL NOT NULL DEFAULT False,
    "expiration" TIMESTAMPTZ,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "user_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "api_user_token"."ctime" IS '创建时间';
COMMENT ON COLUMN "api_user_token"."utime" IS '更新时间';
COMMENT ON COLUMN "api_user_token"."deleted" IS '删除标识';
COMMENT ON COLUMN "api_user_token"."name" IS 'API用户名称';
COMMENT ON COLUMN "api_user_token"."token" IS 'API用户Token';
COMMENT ON COLUMN "api_user_token"."description" IS 'API用户描述';
COMMENT ON COLUMN "api_user_token"."status" IS 'Token是否可用';
COMMENT ON COLUMN "api_user_token"."expiration" IS 'Token过期时间';
COMMENT ON COLUMN "api_user_token"."creator_id" IS '创建人';
COMMENT ON COLUMN "api_user_token"."user_id" IS '创建人';
COMMENT ON TABLE "api_user_token" IS 'API用户Token管理表';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "api_user_token";"""
