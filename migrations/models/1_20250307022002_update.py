from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "account" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "username" VARCHAR(32) NOT NULL UNIQUE,
    "email" VARCHAR(255) NOT NULL UNIQUE,
    "phone" VARCHAR(15) UNIQUE,
    "password" VARCHAR(255) NOT NULL,
    "oauth_id" VARCHAR(255) UNIQUE,
    "disabled" BOOL NOT NULL DEFAULT False
);
COMMENT ON COLUMN "account"."ctime" IS '创建时间';
COMMENT ON COLUMN "account"."utime" IS '更新时间';
COMMENT ON COLUMN "account"."deleted" IS '删除标识';
COMMENT ON TABLE "account" IS '账号模型，包含以下属性：用户名、邮箱、手机号、密码、OAuth ID、是否禁用、角色';
        CREATE TABLE IF NOT EXISTS "authentication_session" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "expiration_date" TIMESTAMPTZ,
    "active" BOOL NOT NULL DEFAULT True,
    "ip" VARCHAR(16) NOT NULL,
    "refresh_expiration_date" TIMESTAMPTZ,
    "requires_second_factor" BOOL NOT NULL DEFAULT False,
    "bearer_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "authentication_session"."ctime" IS '创建时间';
COMMENT ON COLUMN "authentication_session"."utime" IS '更新时间';
COMMENT ON COLUMN "authentication_session"."deleted" IS '删除标识';
COMMENT ON TABLE "authentication_session" IS 'Used to authenticate and identify a client.';
        CREATE TABLE IF NOT EXISTS "role" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "description" VARCHAR(255),
    "permissions" JSONB
);
COMMENT ON COLUMN "role"."ctime" IS '创建时间';
COMMENT ON COLUMN "role"."utime" IS '更新时间';
COMMENT ON COLUMN "role"."deleted" IS '删除标识';
COMMENT ON TABLE "role" IS 'Assigned to an account to authorize an action.';
        CREATE TABLE IF NOT EXISTS "two_step_session" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "expiration_date" TIMESTAMPTZ,
    "active" BOOL NOT NULL DEFAULT True,
    "ip" VARCHAR(16) NOT NULL,
    "attempts" INT NOT NULL DEFAULT 0,
    "code" VARCHAR(6),
    "bearer_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "two_step_session"."ctime" IS '创建时间';
COMMENT ON COLUMN "two_step_session"."utime" IS '更新时间';
COMMENT ON COLUMN "two_step_session"."deleted" IS '删除标识';
COMMENT ON TABLE "two_step_session" IS 'Validates client using a code sent via email or text.';
        DROP TABLE IF EXISTS "user";
        CREATE TABLE "account_role" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "authentication_session";
        DROP TABLE IF EXISTS "two_step_session";
        DROP TABLE IF EXISTS "role";
        DROP TABLE IF EXISTS "account";"""
