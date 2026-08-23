from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "knowledge_space" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "space_key" VARCHAR(64) NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "description" TEXT,
    "source_type" VARCHAR(20) NOT NULL DEFAULT 'manual',
    "source_ref" VARCHAR(128),
    CONSTRAINT "uid_knowledge_s_tenant__e9f8b5" UNIQUE ("tenant_id", "space_key")
);
COMMENT ON COLUMN "knowledge_space"."ctime" IS '创建时间';
COMMENT ON COLUMN "knowledge_space"."utime" IS '更新时间';
COMMENT ON COLUMN "knowledge_space"."deleted" IS '删除标识';
COMMENT ON COLUMN "knowledge_space"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "knowledge_space"."space_key" IS '空间标识，租户内唯一，用于 URI';
COMMENT ON COLUMN "knowledge_space"."name" IS '显示名';
COMMENT ON COLUMN "knowledge_space"."description" IS '用途说明；注入 Agent 系统提示，作为选择空间的依据';
COMMENT ON COLUMN "knowledge_space"."source_type" IS '来源: manual | skill_package';
COMMENT ON COLUMN "knowledge_space"."source_ref" IS 'source_type=skill_package 时为 skill_key';
COMMENT ON TABLE "knowledge_space" IS '租户级知识空间：知识内容的组织与授权单元。';
        CREATE TABLE IF NOT EXISTS "knowledge_document" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
    "space_key" VARCHAR(64) NOT NULL,
    "rel_path" VARCHAR(512) NOT NULL,
    "uri" VARCHAR(1024) NOT NULL,
    "title" VARCHAR(255) NOT NULL,
    "mime_type" VARCHAR(128),
    "size" BIGINT NOT NULL DEFAULT 0,
    "content_hash" VARCHAR(64) NOT NULL,
    "source" VARCHAR(20) NOT NULL DEFAULT 'manual',
    "source_ref" VARCHAR(255),
    "uploader_id" VARCHAR(36),
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "task_id" VARCHAR(128),
    "error" TEXT,
    "indexed_at" TIMESTAMPTZ,
    "space_id" VARCHAR(36) NOT NULL REFERENCES "knowledge_space" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_knowledge_d_tenant__3021f4" UNIQUE ("tenant_id", "uri")
);
CREATE INDEX IF NOT EXISTS "idx_knowledge_d_tenant__840669" ON "knowledge_document" ("tenant_id", "space_key", "status");
COMMENT ON COLUMN "knowledge_document"."ctime" IS '创建时间';
COMMENT ON COLUMN "knowledge_document"."utime" IS '更新时间';
COMMENT ON COLUMN "knowledge_document"."deleted" IS '删除标识';
COMMENT ON COLUMN "knowledge_document"."tenant_id" IS '默认租户ID';
COMMENT ON COLUMN "knowledge_document"."space_key" IS '冗余存储，便于按空间查询';
COMMENT ON COLUMN "knowledge_document"."rel_path" IS '空间内相对路径';
COMMENT ON COLUMN "knowledge_document"."uri" IS '完整 viking:// URI，内部使用';
COMMENT ON COLUMN "knowledge_document"."title" IS '显示名，默认取文件名';
COMMENT ON COLUMN "knowledge_document"."size" IS '字节数';
COMMENT ON COLUMN "knowledge_document"."content_hash" IS 'SHA256，用于幂等判重';
COMMENT ON COLUMN "knowledge_document"."source" IS '来源: manual | api | skill_package | agent | external';
COMMENT ON COLUMN "knowledge_document"."source_ref" IS 'skill_key / 剧本 run_id / 员工 id';
COMMENT ON COLUMN "knowledge_document"."uploader_id" IS 'Account.id';
COMMENT ON COLUMN "knowledge_document"."status" IS 'pending | processing | ready | failed';
COMMENT ON COLUMN "knowledge_document"."task_id" IS 'OpenViking 异步任务 id';
COMMENT ON COLUMN "knowledge_document"."error" IS '失败原因，截断 1000 字符';
COMMENT ON COLUMN "knowledge_document"."indexed_at" IS '索引完成时间';
COMMENT ON TABLE "knowledge_document" IS '知识文档元数据与索引状态。';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "knowledge_document";
        DROP TABLE IF EXISTS "knowledge_space";"""
