-- upgrade --
CREATE TABLE IF NOT EXISTS "embedding_config" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT FALSE,
    "tenant_id" VARCHAR(36) NOT NULL,
    "content" TEXT NOT NULL,
    "content_hash" VARCHAR(64) NOT NULL,
    "dimension" INT NOT NULL,
    "applied_at" TIMESTAMPTZ,
    "applied_by" VARCHAR(36),
    "remark" TEXT
);

CREATE INDEX IF NOT EXISTS "idx_embedding_config_tenant" ON "embedding_config" ("tenant_id");
CREATE INDEX IF NOT EXISTS "idx_embedding_config_deleted" ON "embedding_config" ("deleted");

CREATE TABLE IF NOT EXISTS "reindex_task" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT FALSE,
    "config_id" VARCHAR(36) NOT NULL,
    "tenant_id" VARCHAR(36) NOT NULL,
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "total_workspaces" INT NOT NULL DEFAULT 0,
    "processed_workspaces" INT NOT NULL DEFAULT 0,
    "failed_workspaces" INT NOT NULL DEFAULT 0,
    "details" JSONB NOT NULL DEFAULT '[]'::jsonb,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "error" TEXT
);

CREATE INDEX IF NOT EXISTS "idx_reindex_task_config" ON "reindex_task" ("config_id");
CREATE INDEX IF NOT EXISTS "idx_reindex_task_tenant" ON "reindex_task" ("tenant_id");
CREATE INDEX IF NOT EXISTS "idx_reindex_task_status" ON "reindex_task" ("status");
CREATE INDEX IF NOT EXISTS "idx_reindex_task_deleted" ON "reindex_task" ("deleted");

-- downgrade --
DROP TABLE IF EXISTS "reindex_task";
DROP TABLE IF EXISTS "embedding_config";
