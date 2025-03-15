from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "variables" ADD "value" JSONB NOT NULL;
        ALTER TABLE "variables" ADD "updator_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "variables" DROP COLUMN "type";
        DROP TABLE IF EXISTS "elements";
        ALTER TABLE "variables" ADD CONSTRAINT "fk_variable_account_7dfe9c64" FOREIGN KEY ("updator_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "variables" DROP CONSTRAINT IF EXISTS "fk_variable_account_7dfe9c64";
        ALTER TABLE "variables" ADD "type" VARCHAR(10) NOT NULL;
        ALTER TABLE "variables" DROP COLUMN "value";
        ALTER TABLE "variables" DROP COLUMN "updator_id";"""
