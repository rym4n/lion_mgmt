from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "embedding_config" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(36) NOT NULL UNIQUE,
    "content" TEXT NOT NULL,
    "content_hash" VARCHAR(64) NOT NULL,
    "dimension" INT NOT NULL DEFAULT 2048,
    "applied_at" TIMESTAMPTZ,
    "applied_by" VARCHAR(36),
    "remark" TEXT
);
CREATE INDEX IF NOT EXISTS "idx_embedding_c_tenant__40d2c6" ON "embedding_config" ("tenant_id");
COMMENT ON COLUMN "embedding_config"."ctime" IS '创建时间';
COMMENT ON COLUMN "embedding_config"."utime" IS '更新时间';
COMMENT ON COLUMN "embedding_config"."deleted" IS '删除标识';
COMMENT ON TABLE "embedding_config" IS 'OpenViking embedding 配置（全局单例，维度固定 2048）';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "embedding_config";"""
