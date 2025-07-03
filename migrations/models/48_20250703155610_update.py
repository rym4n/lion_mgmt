from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" ADD "process_record_id" VARCHAR(36);
        CREATE TABLE IF NOT EXISTS "process_records" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "agree" BOOL NOT NULL,
    "remark" TEXT NOT NULL,
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE,
    "processor_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "approvement"."process_record_id" IS '审核处理记录';
COMMENT ON COLUMN "process_records"."ctime" IS '创建时间';
COMMENT ON COLUMN "process_records"."utime" IS '更新时间';
COMMENT ON COLUMN "process_records"."deleted" IS '删除标识';
COMMENT ON COLUMN "process_records"."agree" IS '审核意见，True代表通过，False代表拒绝';
COMMENT ON COLUMN "process_records"."remark" IS '审核结果备注';
COMMENT ON TABLE "process_records" IS '人工审核处理人模型';
        DROP TABLE IF EXISTS "approve_processor";
        ALTER TABLE "approvement" ADD CONSTRAINT "fk_approvem_process__24a64309" FOREIGN KEY ("process_record_id") REFERENCES "process_records" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" DROP CONSTRAINT IF EXISTS "fk_approvem_process__24a64309";
        ALTER TABLE "approvement" DROP COLUMN "process_record_id";
        DROP TABLE IF EXISTS "process_records";"""
