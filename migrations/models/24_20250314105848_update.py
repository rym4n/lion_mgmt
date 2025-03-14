from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "audit_log" ADD "username" VARCHAR(32) NOT NULL;
        CREATE INDEX IF NOT EXISTS "idx_audit_log_usernam_b6341e" ON "audit_log" ("username");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_audit_log_usernam_b6341e";
        ALTER TABLE "audit_log" DROP COLUMN "username";"""
