from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "approvement_process_records";
        ALTER TABLE "approvement" ADD "process_records" JSONB NOT NULL;
        DROP TABLE IF EXISTS "process_records";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" DROP COLUMN "process_records";
        CREATE TABLE "approvement_process_records" (
    "approvement_id" VARCHAR(36) NOT NULL REFERENCES "approvement" ("id") ON DELETE CASCADE,
    "processrecords_id" VARCHAR(36) NOT NULL REFERENCES "process_records" ("id") ON DELETE CASCADE
);"""
