from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE INDEX IF NOT EXISTS "idx_runlog_tenant__ade788"
        ON "runlog" (
            "tenant_id",
            "event_id",
            "deleted",
            "run_id",
            "start_time" DESC,
            "ctime" DESC,
            "id"
        );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return 'DROP INDEX IF EXISTS "idx_runlog_tenant__ade788";'
