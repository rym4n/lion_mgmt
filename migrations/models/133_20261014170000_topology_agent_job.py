"""Schema for durable topology agent normalization jobs."""

from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "network_topology_agent_job" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT FALSE,
            "tenant_id" VARCHAR(255) NOT NULL,
            "analysis_run_id" VARCHAR(36) NOT NULL,
            "topology_id" VARCHAR(36) NOT NULL,
            "collection_run_id" VARCHAR(36),
            "attempt" INT NOT NULL,
            "state" VARCHAR(16) NOT NULL DEFAULT 'QUEUED',
            "resource_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "job_token_hash" VARCHAR(64) NOT NULL,
            "profile_version" VARCHAR(255),
            "cache_hits" INT NOT NULL DEFAULT 0,
            "cache_misses" INT NOT NULL DEFAULT 0,
            "iterations" INT NOT NULL DEFAULT 0,
            "token_usage" JSONB,
            "documents" TEXT,
            "transcript" TEXT,
            "error_code" VARCHAR(128),
            "error_message" TEXT,
            "started_at" TIMESTAMPTZ,
            "finished_at" TIMESTAMPTZ
        );

        CREATE INDEX IF NOT EXISTS "idx_topology_agent_job_tenant_analysis"
            ON "network_topology_agent_job" ("tenant_id", "analysis_run_id");
        CREATE INDEX IF NOT EXISTS "idx_topology_agent_job_tenant_collection"
            ON "network_topology_agent_job" ("tenant_id", "collection_run_id");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return 'DROP TABLE IF EXISTS "network_topology_agent_job";'
