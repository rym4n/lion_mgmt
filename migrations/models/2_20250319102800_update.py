from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "tool_version_number" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tool_name" VARCHAR(255),
    "upload_time" VARCHAR(50),
    "number_of_uploads" INT
);
COMMENT ON COLUMN "tool_version_number"."ctime" IS '创建时间';
COMMENT ON COLUMN "tool_version_number"."utime" IS '更新时间';
COMMENT ON COLUMN "tool_version_number"."deleted" IS '删除标识';
COMMENT ON COLUMN "tool_version_number"."tool_name" IS '工具名称';
COMMENT ON COLUMN "tool_version_number"."upload_time" IS '上传时间';
COMMENT ON COLUMN "tool_version_number"."number_of_uploads" IS '上传次数';
COMMENT ON TABLE "tool_version_number" IS '工具版本号生成序列号管理';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "tool_version_number";"""
