from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "audit" ALTER COLUMN "action" TYPE VARCHAR(50) USING "action"::VARCHAR(50);
        ALTER TABLE "audit" ALTER COLUMN "client_ip" TYPE VARCHAR(50) USING "client_ip"::VARCHAR(50);
        ALTER TABLE "audit" ALTER COLUMN "module" TYPE VARCHAR(80) USING "module"::VARCHAR(80);
        ALTER TABLE "audit" ALTER COLUMN "result" TYPE VARCHAR(20) USING "result"::VARCHAR(20);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "audit" ALTER COLUMN "action" TYPE VARCHAR(255) USING "action"::VARCHAR(255);
        ALTER TABLE "audit" ALTER COLUMN "client_ip" TYPE VARCHAR(255) USING "client_ip"::VARCHAR(255);
        ALTER TABLE "audit" ALTER COLUMN "module" TYPE VARCHAR(255) USING "module"::VARCHAR(255);
        ALTER TABLE "audit" ALTER COLUMN "result" TYPE VARCHAR(255) USING "result"::VARCHAR(255);"""
