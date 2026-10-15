from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

# The installer/upgrade bootstrap pre-creates this index concurrently on an
# existing PostgreSQL runlog table, then Aerich records this migration idempotently.


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE INDEX IF NOT EXISTS "idx_runlog_tenant__c05b58"
        ON "runlog" (
            "tenant_id", "timer_id", "deleted", "run_id",
            "start_time" DESC, "ctime" DESC
        );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return 'DROP INDEX IF EXISTS "idx_runlog_tenant__c05b58";'
