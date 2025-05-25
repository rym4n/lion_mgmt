from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_audit_log_module_a9ee07";
        ALTER TABLE "audit_log" DROP COLUMN "action";
        ALTER TABLE "audit_log" DROP COLUMN "module";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "audit_log" ADD "action" VARCHAR(50) NOT NULL;
        ALTER TABLE "audit_log" ADD "module" VARCHAR(80) NOT NULL;
        CREATE INDEX IF NOT EXISTS "idx_audit_log_module_a9ee07" ON "audit_log" ("module");"""
