from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" DROP CONSTRAINT IF EXISTS "fk_approvem_account_702806b5";
        DROP TABLE IF EXISTS "approve_accounts";
        ALTER TABLE "approvement" ADD "approve_type" INT NOT NULL DEFAULT 0;
        ALTER TABLE "approvement" DROP COLUMN "processor_id";
        COMMENT ON COLUMN "approvement"."status" IS '审核状态，0代表待处理，1代表通过，2代表拒绝';
        CREATE TABLE "approvement_account" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "approvement_account";
        ALTER TABLE "approvement" ADD "processor_id" VARCHAR(36);
        ALTER TABLE "approvement" DROP COLUMN "approve_type";
        COMMENT ON COLUMN "approvement"."status" IS '审核状态，0代表待审核，1代表审核通过，2代表审核拒绝，3代表审核过期';
        CREATE TABLE "approve_accounts" (
    "account_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE
);
        ALTER TABLE "approvement" ADD CONSTRAINT "fk_approvem_account_702806b5" FOREIGN KEY ("processor_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""
