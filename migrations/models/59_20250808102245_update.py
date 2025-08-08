from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" ADD "playbook_version_id" VARCHAR(36);
        ALTER TABLE "runlog" DROP COLUMN "playbook_version";
        ALTER TABLE "runlog" ADD CONSTRAINT "fk_runlog_playbook_126a9048" FOREIGN KEY ("playbook_version_id") REFERENCES "playbook_version" ("id") ON DELETE SET NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" DROP CONSTRAINT IF EXISTS "fk_runlog_playbook_126a9048";
        ALTER TABLE "runlog" ADD "playbook_version" VARCHAR(255);
        ALTER TABLE "runlog" DROP COLUMN "playbook_version_id";"""
