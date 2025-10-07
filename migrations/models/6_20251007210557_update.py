from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" ALTER COLUMN "run_id" TYPE VARCHAR(20) USING "run_id"::VARCHAR(20);
        ALTER TABLE "approvement" ALTER COLUMN "audit_node" TYPE VARCHAR(36) USING "audit_node"::VARCHAR(36);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" ALTER COLUMN "run_id" TYPE VARCHAR(50) USING "run_id"::VARCHAR(50);
        ALTER TABLE "approvement" ALTER COLUMN "audit_node" TYPE VARCHAR(50) USING "audit_node"::VARCHAR(50);"""
