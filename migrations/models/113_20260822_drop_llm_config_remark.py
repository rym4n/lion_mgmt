from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "llm_model_config" DROP COLUMN "remark";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "llm_model_config" ADD COLUMN "remark" VARCHAR(512);
        COMMENT ON COLUMN "llm_model_config"."remark" IS '备注';
    """
