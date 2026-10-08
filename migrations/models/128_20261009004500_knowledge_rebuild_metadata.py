from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "knowledge_document"
        ADD COLUMN IF NOT EXISTS "ingest_attempt_id" VARCHAR(32);
        ALTER TABLE "knowledge_document"
        ADD COLUMN IF NOT EXISTS "filename" VARCHAR(255);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "knowledge_document"
        DROP COLUMN IF EXISTS "filename";
        ALTER TABLE "knowledge_document"
        DROP COLUMN IF EXISTS "ingest_attempt_id";
    """
