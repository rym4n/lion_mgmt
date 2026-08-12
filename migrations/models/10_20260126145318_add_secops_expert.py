from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE EXTENSION IF NOT EXISTS vector;
        
        CREATE TABLE IF NOT EXISTS "secops_agent_mapping" (
            "id" VARCHAR(64) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(64) NOT NULL,
            "warroom_id" VARCHAR(64) NOT NULL UNIQUE REFERENCES "room" ("id") ON DELETE CASCADE,
            "agent_id" VARCHAR(64) NOT NULL,
            "status" VARCHAR(20) NOT NULL DEFAULT 'active',
            "archived_at" TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS "idx_secops_agent_mapping_tenant" ON "secops_agent_mapping" ("tenant_id");
        CREATE INDEX IF NOT EXISTS "idx_secops_agent_mapping_status" ON "secops_agent_mapping" ("status");
        
        CREATE TABLE IF NOT EXISTS "archival_knowledge" (
            "id" VARCHAR(64) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(64) NOT NULL,
            "content" TEXT NOT NULL,
            "embedding" vector(2560),
            "knowledge_type" VARCHAR(20) NOT NULL,
            "attack_type" VARCHAR(50),
            "tags" JSONB DEFAULT '[]',
            "source_warroom_id" VARCHAR(64),
            "source_event_id" VARCHAR(64),
            "submitted_by" VARCHAR(64),
            "review_status" VARCHAR(20) NOT NULL DEFAULT 'pending',
            "reviewed_by_id" VARCHAR(64) REFERENCES "account" ("id"),
            "reviewed_at" TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS "idx_archival_knowledge_tenant" ON "archival_knowledge" ("tenant_id");
        CREATE INDEX IF NOT EXISTS "idx_archival_knowledge_type" ON "archival_knowledge" ("knowledge_type");
        CREATE INDEX IF NOT EXISTS "idx_archival_knowledge_attack" ON "archival_knowledge" ("attack_type");
        CREATE INDEX IF NOT EXISTS "idx_archival_knowledge_status" ON "archival_knowledge" ("review_status");
        
        CREATE TABLE IF NOT EXISTS "secops_user_preference" (
            "id" VARCHAR(64) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(64) NOT NULL,
            "account_id" VARCHAR(64) NOT NULL UNIQUE REFERENCES "account" ("id") ON DELETE CASCADE,
            "notify_channel" VARCHAR(20) NOT NULL DEFAULT 'in_app',
            "notify_config" JSONB DEFAULT '{}',
            "report_language" VARCHAR(10) NOT NULL DEFAULT 'zh-CN',
            "response_verbosity" VARCHAR(20) NOT NULL DEFAULT 'normal',
            "skill_tags" JSONB DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS "idx_secops_user_pref_tenant" ON "secops_user_preference" ("tenant_id");
        
        CREATE OR REPLACE FUNCTION search_archival_knowledge(
            query_embedding vector(2560),
            tenant_id_filter VARCHAR(64),
            knowledge_type_filter VARCHAR(20) DEFAULT NULL,
            limit_count INT DEFAULT 5
        )
        RETURNS TABLE (
            id VARCHAR(64),
            content TEXT,
            knowledge_type VARCHAR(20),
            attack_type VARCHAR(50),
            similarity FLOAT
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT 
                ak.id,
                ak.content,
                ak.knowledge_type,
                ak.attack_type,
                1 - (ak.embedding <=> query_embedding) as similarity
            FROM archival_knowledge ak
            WHERE ak.tenant_id = tenant_id_filter
              AND ak.deleted = false
              AND ak.review_status = 'approved'
              AND (knowledge_type_filter IS NULL OR ak.knowledge_type = knowledge_type_filter)
            ORDER BY ak.embedding <=> query_embedding
            LIMIT limit_count;
        END;
        $$ LANGUAGE plpgsql;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP FUNCTION IF EXISTS search_archival_knowledge;
        DROP TABLE IF EXISTS "secops_user_preference";
        DROP TABLE IF EXISTS "archival_knowledge";
        DROP TABLE IF EXISTS "secops_agent_mapping";
    """


MODELS_STATE = (
    "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"
    "RZx7Jz+MU6h8uWwSO2DB4+pOzr/wOBo8ot"
)
