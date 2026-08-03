from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE INDEX IF NOT EXISTS "idx_runlog_tenant__0d09ad"
        ON "runlog" ("tenant_id", "run_id", "ctime", "id");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return 'DROP INDEX IF EXISTS "idx_runlog_tenant__0d09ad";'
