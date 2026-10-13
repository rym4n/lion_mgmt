from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "command_center_agent"
        ADD COLUMN IF NOT EXISTS "max_iters" INT NOT NULL DEFAULT 200;
        ALTER TABLE "command_center_task"
        ADD COLUMN IF NOT EXISTS "timeout_seconds" INT NOT NULL DEFAULT 3600;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "command_center_task"
        DROP COLUMN IF EXISTS "timeout_seconds";
        ALTER TABLE "command_center_agent"
        DROP COLUMN IF EXISTS "max_iters";
    """
