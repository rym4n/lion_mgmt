"""Persist direct collection cron cursors; retire legacy jobs in the listener."""
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True
TABLES = ("network_topology_collection_plan", "network_access_policy_collection_plan")


async def upgrade(db: BaseDBAsyncClient) -> str:
    return "\n".join(
        f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "scheduled_cron" VARCHAR(100);\n'
        f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "next_run_at" TIMESTAMPTZ;\n'
        for table in TABLES
    )


async def downgrade(db: BaseDBAsyncClient) -> str:
    return "\n".join(
        f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS "scheduled_cron";\n'
        f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS "next_run_at";\n'
        for table in TABLES
    )
