from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" ALTER COLUMN "flow_id" DROP NOT NULL;
        ALTER TABLE "approvement" ALTER COLUMN "run_id" TYPE VARCHAR(64);
        ALTER TABLE "approvement" ALTER COLUMN "callback_token" TYPE VARCHAR(64);
        COMMENT ON COLUMN "approvement"."params" IS '自定义参数；approval_kind=playbook/agent 用于区分剧本审批和 Agent 审批';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "approvement" ALTER COLUMN "flow_id" SET NOT NULL;
        ALTER TABLE "approvement" ALTER COLUMN "run_id" TYPE VARCHAR(20);
        ALTER TABLE "approvement" ALTER COLUMN "callback_token" TYPE VARCHAR(16);
        COMMENT ON COLUMN "approvement"."params" IS '自定义参数';
    """
