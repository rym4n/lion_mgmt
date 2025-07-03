from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_permissions" ALTER COLUMN "permissions" SET NOT NULL;
        ALTER TABLE "playbook_permissions" ALTER COLUMN "permissions" TYPE VARCHAR(30) USING "permissions"::VARCHAR(30);
        COMMENT ON COLUMN "playbook_permissions"."permissions" IS '剧本权限';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "playbook_permissions" ALTER COLUMN "permissions" TYPE JSONB USING "permissions"::JSONB;
        ALTER TABLE "playbook_permissions" ALTER COLUMN "permissions" DROP NOT NULL;
        COMMENT ON COLUMN "playbook_permissions"."permissions" IS NULL;"""
