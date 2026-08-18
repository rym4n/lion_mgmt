from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "package_ingest_outbox" (
            "id" UUID NOT NULL PRIMARY KEY,
            "tenant_id" VARCHAR(64) NOT NULL,
            "package_identity" VARCHAR(255) NOT NULL,
            "file_path" TEXT NOT NULL,
            "content_hash" VARCHAR(64) NOT NULL,
            "target_uri" TEXT NOT NULL,
            "task_id" VARCHAR(255),
            "status" VARCHAR(32) NOT NULL DEFAULT 'pending',
            "error" TEXT,
            "attempt_count" INT NOT NULL DEFAULT 0,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS "idx_package_ingest_outbox_tenant_package" ON "package_ingest_outbox" ("tenant_id", "package_identity");
        CREATE INDEX IF NOT EXISTS "idx_package_ingest_outbox_status" ON "package_ingest_outbox" ("status", "attempt_count");
        CREATE UNIQUE INDEX IF NOT EXISTS "idx_package_ingest_outbox_idempotency" ON "package_ingest_outbox" ("tenant_id", "package_identity", "content_hash");
        COMMENT ON TABLE "package_ingest_outbox" IS 'OpenViking knowledge ingest outbox for package docs';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "package_ingest_outbox";
    """
