from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" ALTER COLUMN "playbook_version_id" TYPE VARCHAR(36) USING "playbook_version_id"::VARCHAR(36);
        COMMENT ON COLUMN "runlog"."playbook_version_id" IS '关联剧本版本';
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_playbook_126a9048" FOREIGN KEY ("playbook_version_id") REFERENCES "playbook_version" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_playbook_126a9048";
        COMMENT ON COLUMN "runlog"."playbook_version_id" IS '剧本版本id';
        ALTER TABLE "runlog" ALTER COLUMN "playbook_version_id" TYPE VARCHAR(255) USING "playbook_version_id"::VARCHAR(255);"""
