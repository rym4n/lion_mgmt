"""Heartbeat for topology collection background workers."""
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return '''
        ALTER TABLE "network_topology_collection_run"
            ADD COLUMN IF NOT EXISTS "heartbeat_at" TIMESTAMPTZ;
    '''


async def downgrade(db: BaseDBAsyncClient) -> str:
    return 'ALTER TABLE "network_topology_collection_run" DROP COLUMN IF EXISTS "heartbeat_at";'
