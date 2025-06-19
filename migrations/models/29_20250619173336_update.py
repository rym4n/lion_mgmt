from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "playbook_permissions" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "roles_id" JSONB NOT NULL,
    "users_id" JSONB NOT NULL,
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "playbook_permissions"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook_permissions"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook_permissions"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook_permissions"."roles_id" IS '有权限执行的角色';
COMMENT ON COLUMN "playbook_permissions"."users_id" IS '有权限执行的用户';
COMMENT ON COLUMN "playbook_permissions"."playbook_id" IS '剧本';
COMMENT ON TABLE "playbook_permissions" IS '剧本权限表';
        DROP TABLE IF EXISTS "playbook_version_permissions";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "playbook_permissions";"""
