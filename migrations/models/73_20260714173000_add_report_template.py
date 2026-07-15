from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "report_template" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            "owner_id" VARCHAR(36) REFERENCES "account" ("id") ON DELETE SET NULL,
            "document_id" VARCHAR(36) REFERENCES "report_document" ("id") ON DELETE SET NULL,
            "name" VARCHAR(255) NOT NULL,
            "description" TEXT NOT NULL DEFAULT '',
            "content_html" TEXT NOT NULL DEFAULT '',
            "fetch_config" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "annotations" JSONB NOT NULL DEFAULT '[]'::jsonb,
            "source_template_key" VARCHAR(128)
        );
        CREATE INDEX IF NOT EXISTS "idx_report_template_tenant_owner"
            ON "report_template" ("tenant_id", "owner_id");
        ALTER TABLE "report_template"
            ADD COLUMN IF NOT EXISTS "document_id" VARCHAR(36)
            REFERENCES "report_document" ("id") ON DELETE SET NULL;
        COMMENT ON TABLE "report_template" IS '用户在线报告模板表';
        COMMENT ON COLUMN "report_template"."content_html" IS '在线编辑器 HTML 内容';
        COMMENT ON COLUMN "report_template"."fetch_config" IS '取数占位符动作配置';
        COMMENT ON COLUMN "report_template"."annotations" IS '文档批注';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "report_template";
    """
