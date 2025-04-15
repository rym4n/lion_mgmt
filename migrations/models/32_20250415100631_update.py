from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ALTER COLUMN "status" DROP DEFAULT;
        COMMENT ON COLUMN "playbook"."status" IS '剧本状态';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook" ALTER COLUMN "status" SET DEFAULT 0;
        COMMENT ON COLUMN "playbook"."status" IS '剧本状态,0草稿,1已发布';"""
