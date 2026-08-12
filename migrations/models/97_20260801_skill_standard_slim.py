from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "digital_employee_skill" DROP COLUMN IF EXISTS "bindings";
        ALTER TABLE "digital_employee_skill" DROP COLUMN IF EXISTS "require_approval";
        ALTER TABLE "digital_employee_skill" DROP COLUMN IF EXISTS "meta";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "digital_employee_skill" ADD COLUMN IF NOT EXISTS "bindings" JSONB NOT NULL DEFAULT '{}'::jsonb;
        ALTER TABLE "digital_employee_skill" ADD COLUMN IF NOT EXISTS "require_approval" BOOL NOT NULL DEFAULT False;
        ALTER TABLE "digital_employee_skill" ADD COLUMN IF NOT EXISTS "meta" JSONB NOT NULL DEFAULT '{}'::jsonb;
    """
