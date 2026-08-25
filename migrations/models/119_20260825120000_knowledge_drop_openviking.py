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
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_knowledge_d_tenant__hash";
        ALTER TABLE "knowledge_document" DROP COLUMN IF EXISTS "storage_key";
        ALTER TABLE "knowledge_document" ADD COLUMN IF NOT EXISTS "rel_path" VARCHAR(512) NOT NULL DEFAULT '';
        ALTER TABLE "knowledge_document" ADD COLUMN IF NOT EXISTS "uri" VARCHAR(1024) NOT NULL DEFAULT '';
        ALTER TABLE "knowledge_document" ADD COLUMN IF NOT EXISTS "task_id" VARCHAR(128);
    """
