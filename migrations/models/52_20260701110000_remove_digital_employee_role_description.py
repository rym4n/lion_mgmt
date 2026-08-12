from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "digital_employee_role"
        SET "meta" = "meta" - 'description',
            "utime" = CURRENT_TIMESTAMP
        WHERE "meta" ? 'description';

        ALTER TABLE "digital_employee_role" DROP COLUMN IF EXISTS "description";

        UPDATE "command_center_agent"
        SET "description" = NULL,
            "utime" = CURRENT_TIMESTAMP
        WHERE "role_key" IS NOT NULL
          AND "description" IS NOT NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "digital_employee_role" ADD COLUMN IF NOT EXISTS "description" TEXT;
    """
