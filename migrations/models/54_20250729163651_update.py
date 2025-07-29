from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_playbook_31ff23a5";
        ALTER TABLE "runlog" RENAME COLUMN "flow_id" TO "playbook_version_id";
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_playbook_126a9048" FOREIGN KEY ("playbook_version_id") REFERENCES "playbook_version" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_playbook_126a9048";
        ALTER TABLE "runlog" RENAME COLUMN "playbook_version_id" TO "flow_id";
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_playbook_31ff23a5" FOREIGN KEY ("flow_id") REFERENCES "playbook_version" ("id") ON DELETE CASCADE;"""
