from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "variables" DROP CONSTRAINT IF EXISTS "fk_variable_account_7d8a5f50";
        ALTER TABLE "variables" DROP COLUMN "creator_id";
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_variables_name_4779ca" ON "variables" ("name");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_variables_name_4779ca";
        ALTER TABLE "variables" ADD "creator_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "variables" ADD CONSTRAINT "fk_variable_account_7d8a5f50" FOREIGN KEY ("creator_id") REFERENCES "account" ("id") ON DELETE CASCADE;"""
