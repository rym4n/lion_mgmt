from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return 'ALTER TABLE "runlog" ADD COLUMN IF NOT EXISTS "output_params" JSONB;'


async def downgrade(db: BaseDBAsyncClient) -> str:
    return 'ALTER TABLE "runlog" DROP COLUMN IF EXISTS "output_params";'
