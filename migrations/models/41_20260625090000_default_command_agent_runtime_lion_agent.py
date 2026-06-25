from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "command_center_agent" ALTER COLUMN "runtime" SET DEFAULT 'lion_agent';
        UPDATE "command_center_agent" SET "runtime" = 'lion_agent' WHERE "runtime" = 'nanobot';
        COMMENT ON COLUMN "command_center_agent"."system_instruction" IS '调用lion_agent时使用的system_instruction提示词';
        COMMENT ON COLUMN "command_center_agent"."runtime" IS '运行时类型: lion_agent 等';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "command_center_agent" ALTER COLUMN "runtime" SET DEFAULT 'nanobot';
        COMMENT ON COLUMN "command_center_agent"."system_instruction" IS '调用Nanobot时使用的system_instruction提示词';
        COMMENT ON COLUMN "command_center_agent"."runtime" IS '运行时类型: nanobot等';"""
