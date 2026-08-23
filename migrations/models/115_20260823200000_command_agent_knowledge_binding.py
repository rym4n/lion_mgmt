from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "command_center_agent" ADD COLUMN "knowledge_space_keys" JSONB NOT NULL DEFAULT '[]'::jsonb;
        COMMENT ON COLUMN "command_center_agent"."knowledge_space_keys" IS '可见知识空间 key 列表';
        ALTER TABLE "command_center_agent" ADD COLUMN "knowledge_write_space" VARCHAR(64);
        COMMENT ON COLUMN "command_center_agent"."knowledge_write_space" IS '员工写入的目标空间 key，为空则不给写入工具';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "command_center_agent" DROP COLUMN "knowledge_write_space";
        ALTER TABLE "command_center_agent" DROP COLUMN "knowledge_space_keys";
    """
