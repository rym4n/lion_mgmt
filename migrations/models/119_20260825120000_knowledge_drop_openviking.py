from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    # 知识空间模块未上线，直接改表结构，不做数据搬迁（SPEC §8 已确认）。
    # 既有行的 uri/rel_path 无保留价值，且新唯一键与旧数据可能冲突，
    # 因此先清空文档表。
    return """
        DELETE FROM "knowledge_document";
        ALTER TABLE "knowledge_document" DROP CONSTRAINT IF EXISTS
            "uid_knowledge_d_tenant__3021f4";
        ALTER TABLE "knowledge_document" DROP COLUMN IF EXISTS "rel_path";
        ALTER TABLE "knowledge_document" DROP COLUMN IF EXISTS "uri";
        ALTER TABLE "knowledge_document" DROP COLUMN IF EXISTS "task_id";
        ALTER TABLE "knowledge_document" ADD COLUMN IF NOT EXISTS
            "storage_key" VARCHAR(512) NOT NULL DEFAULT '';
        ALTER TABLE "knowledge_document" ALTER COLUMN "status" SET DEFAULT 'processing';
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_knowledge_d_tenant__hash"
            ON "knowledge_document" ("tenant_id", "space_key", "content_hash");
        DROP TABLE IF EXISTS "tenant_openviking_credential";
        DROP TABLE IF EXISTS "embedding_config";
        CREATE TABLE IF NOT EXISTS "tenant_embedding_config" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(36) NOT NULL UNIQUE,
            "base_url" VARCHAR(512) NOT NULL,
            "model" VARCHAR(128) NOT NULL,
            "dimensions" INT NOT NULL,
            "api_key" VARCHAR(2048) NOT NULL,
            "fingerprint" VARCHAR(32) NOT NULL DEFAULT '',
            "indexed_fingerprint" VARCHAR(32) NOT NULL DEFAULT '',
            "applied_at" TIMESTAMPTZ,
            "applied_by" VARCHAR(36),
            "remark" TEXT
        );
        CREATE INDEX IF NOT EXISTS "idx_tenant_emb_tenant"
            ON "tenant_embedding_config" ("tenant_id");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_knowledge_d_tenant__hash";
        ALTER TABLE "knowledge_document" DROP COLUMN IF EXISTS "storage_key";
        ALTER TABLE "knowledge_document" ADD COLUMN IF NOT EXISTS "rel_path" VARCHAR(512) NOT NULL DEFAULT '';
        ALTER TABLE "knowledge_document" ADD COLUMN IF NOT EXISTS "uri" VARCHAR(1024) NOT NULL DEFAULT '';
        ALTER TABLE "knowledge_document" ADD COLUMN IF NOT EXISTS "task_id" VARCHAR(128);
    """
