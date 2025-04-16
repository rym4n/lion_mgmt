from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "permissions_menu" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "model" VARCHAR(255) NOT NULL UNIQUE,
    "description" VARCHAR(255),
    "Permissions" JSONB
);
COMMENT ON COLUMN "permissions_menu"."ctime" IS '创建时间';
COMMENT ON COLUMN "permissions_menu"."utime" IS '更新时间';
COMMENT ON COLUMN "permissions_menu"."deleted" IS '删除标识';
COMMENT ON TABLE "permissions_menu" IS '权限菜单';
        ALTER TABLE "playbook" DROP COLUMN "version";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ADD "version" INT NOT NULL DEFAULT 0;
        DROP TABLE IF EXISTS "permissions_menu";"""
