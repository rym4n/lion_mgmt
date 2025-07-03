from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "process_records" DROP CONSTRAINT IF EXISTS "fk_process__approvem_4cdbcf9a";
        ALTER TABLE "approvement" DROP CONSTRAINT IF EXISTS "fk_approvem_process__24a64309";
        ALTER TABLE "approvement" DROP COLUMN "process_record_id";
        ALTER TABLE "approvement" DROP COLUMN "agree_count";
        ALTER TABLE "process_records" DROP COLUMN "approvement_id";
        CREATE TABLE "approvement_process_records" (
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE,
    "processrecords_id" VARCHAR(36) NOT NULL REFERENCES "process_records" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "approvement_process_records";
        ALTER TABLE "approvement" ADD "process_record_id" VARCHAR(36);
        ALTER TABLE "approvement" ADD "agree_count" INT NOT NULL DEFAULT 0;
        ALTER TABLE "process_records" ADD "approvement_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "approvement" ADD CONSTRAINT "fk_approvem_process__24a64309" FOREIGN KEY ("process_record_id") REFERENCES "process_records" ("id") ON DELETE CASCADE;
        ALTER TABLE "process_records" ADD CONSTRAINT "fk_process__approvem_4cdbcf9a" FOREIGN KEY ("approvement_id") REFERENCES "approvement" ("id") ON DELETE CASCADE;"""
