from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" ADD "mfa_enable" BOOL NOT NULL DEFAULT False;
        ALTER TABLE "account" ADD "mfa_key" VARCHAR(50);
        ALTER TABLE "account" ALTER COLUMN "password" TYPE VARCHAR(32) USING "password"::VARCHAR(32);
        ALTER TABLE "account" ALTER COLUMN "email" DROP NOT NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "account" DROP COLUMN "mfa_enable";
        ALTER TABLE "account" DROP COLUMN "mfa_key";
        ALTER TABLE "account" ALTER COLUMN "password" TYPE VARCHAR(255) USING "password"::VARCHAR(255);
        ALTER TABLE "account" ALTER COLUMN "email" SET NOT NULL;"""
