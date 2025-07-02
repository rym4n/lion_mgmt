from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "playbook_role";
        DROP TABLE IF EXISTS "playbook_user";
        CREATE TABLE IF NOT EXISTS "playbook_permissions" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "roles" JSONB NOT NULL,
    "users" JSONB NOT NULL,
    "pb_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "playbook_permissions"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook_permissions"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook_permissions"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook_permissions"."roles" IS '有权限执行的角色';
COMMENT ON COLUMN "playbook_permissions"."users" IS '有权限执行的用户';
COMMENT ON COLUMN "playbook_permissions"."pb_id" IS '剧本';
COMMENT ON TABLE "playbook_permissions" IS '剧本权限表';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "playbook_permissions";
        CREATE TABLE "playbook_role" (
    "role_id" VARCHAR(36) NOT NULL REFERENCES "role" ("id") ON DELETE CASCADE,
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE
);
        CREATE TABLE "playbook_user" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE
);"""
