from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "command_center_agent" DROP COLUMN IF EXISTS "system_instruction";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return ""
