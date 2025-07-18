from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uniq_runlog";
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_runlog_run_id_b706a0" ON "runlog" ("run_id", "node_id", "node_name", "resource_name");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_runlog_run_id_b706a0";
        CREATE INDEX IF NOT EXISTS "uniq_runlog" ON "runlog" ("run_id", "node_id", "node_name", "resource_name");"""
