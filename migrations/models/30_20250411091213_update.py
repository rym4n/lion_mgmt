from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "action_permissions" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "action_name" VARCHAR(255) NOT NULL,
    "action_description" VARCHAR(255) NOT NULL,
    "tool_name" VARCHAR(255) NOT NULL,
    "roles_id" JSONB NOT NULL,
    "users_id" JSONB NOT NULL
);
COMMENT ON COLUMN "action_permissions"."ctime" IS '创建时间';
COMMENT ON COLUMN "action_permissions"."utime" IS '更新时间';
COMMENT ON COLUMN "action_permissions"."deleted" IS '删除标识';
COMMENT ON COLUMN "action_permissions"."action_name" IS '动作名称';
COMMENT ON COLUMN "action_permissions"."action_description" IS '动作描述';
COMMENT ON COLUMN "action_permissions"."tool_name" IS '工具名称';
COMMENT ON COLUMN "action_permissions"."roles_id" IS '有权限执行的角色';
COMMENT ON COLUMN "action_permissions"."users_id" IS '有权限执行的用户';
COMMENT ON TABLE "action_permissions" IS '动作权限表';
        ALTER TABLE "tool_action" DROP COLUMN "users_id";
        ALTER TABLE "tool_action" DROP COLUMN "roles_id";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tool_action" ADD "users_id" JSONB;
        ALTER TABLE "tool_action" ADD "roles_id" JSONB;
        DROP TABLE IF EXISTS "action_permissions";"""
