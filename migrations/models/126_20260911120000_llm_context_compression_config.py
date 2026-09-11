from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "llm_model_config"
        ADD COLUMN IF NOT EXISTS "context_size" INT NOT NULL DEFAULT 128000;
        ALTER TABLE "llm_model_config"
        ADD COLUMN IF NOT EXISTS "trigger_ratio" DOUBLE PRECISION NOT NULL DEFAULT 0.8;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "llm_model_config" DROP COLUMN IF EXISTS "trigger_ratio";
        ALTER TABLE "llm_model_config" DROP COLUMN IF EXISTS "context_size";
    """
