from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "approve_processor";
        CREATE TABLE IF NOT EXISTS "approve_processor" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "opinion" INT NOT NULL,
    "remark" TEXT NOT NULL,
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE,
    "processor_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "approve_processor"."ctime" IS '创建时间';
COMMENT ON COLUMN "approve_processor"."utime" IS '更新时间';
COMMENT ON COLUMN "approve_processor"."deleted" IS '删除标识';
COMMENT ON COLUMN "approve_processor"."opinion" IS '审核意见，1代表通过，2代表拒绝';
COMMENT ON COLUMN "approve_processor"."remark" IS '审核结果备注';
COMMENT ON TABLE "approve_processor" IS '人工审核处理人模型';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "approve_processor";
        CREATE TABLE "approve_processor" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE
);"""
