from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "network_topology_analysis_run" ADD COLUMN IF NOT EXISTS "graph_payload" TEXT NULL;
        ALTER TABLE "network_topology_analysis_run" ADD COLUMN IF NOT EXISTS "agent_observation_ids" JSONB NULL;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "network_topology_analysis_run" DROP COLUMN IF EXISTS "agent_observation_ids";
        ALTER TABLE "network_topology_analysis_run" DROP COLUMN IF EXISTS "graph_payload";"""
