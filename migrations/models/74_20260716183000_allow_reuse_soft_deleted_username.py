from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "account" AS account
        SET "deleted" = TRUE,
            "utime" = CURRENT_TIMESTAMP
        WHERE account."type" = 'api'
          AND account."deleted" IS FALSE
          AND NOT EXISTS (
              SELECT 1
              FROM "tenant_member" AS membership
              WHERE membership."account_id" = account."id"
          );

        ALTER TABLE "account" DROP CONSTRAINT IF EXISTS "account_username_key";
        DROP INDEX IF EXISTS "uid_account_usernam_c7a6b4";
        DROP INDEX IF EXISTS "idx_unique_user_active";
        DROP INDEX IF EXISTS "idx_unique_username_active";
        CREATE UNIQUE INDEX "idx_unique_username_active"
            ON "account" ("username")
            WHERE "deleted" IS FALSE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_unique_username_active";
        ALTER TABLE "account"
            ADD CONSTRAINT "account_username_key" UNIQUE ("username");
    """
