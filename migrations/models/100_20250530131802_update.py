from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_playbook_name_7d99d8";
        ALTER TABLE "playbook" DROP CONSTRAINT IF EXISTS "fk_playbook_account_48c30a25";
        ALTER TABLE "playbook" DROP CONSTRAINT IF EXISTS "fk_playbook_account_dc41491a";
        ALTER TABLE "playbook" DROP COLUMN "status";
        ALTER TABLE "playbook" DROP COLUMN "version";
        ALTER TABLE "playbook" DROP COLUMN "creator_id";
        ALTER TABLE "playbook" DROP COLUMN "updator_id";
        ALTER TABLE "playbook" DROP COLUMN "flow_json";
        ALTER TABLE "playbook" DROP COLUMN "tags";
        CREATE TABLE IF NOT EXISTS "playbook_version" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "version" VARCHAR(30) NOT NULL DEFAULT '0',
    "status" INT NOT NULL DEFAULT 0,
    "flow_json" JSONB NOT NULL,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_playbook_ve_playboo_808e98" UNIQUE ("playbook_id", "version")
);
COMMENT ON COLUMN "playbook_version"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook_version"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook_version"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook_version"."version" IS '版本号，每次publish都会生成新纪录，用于版本回溯';
COMMENT ON COLUMN "playbook_version"."status" IS '剧本状态,0草稿,1已发布';
COMMENT ON COLUMN "playbook_version"."flow_json" IS '流程图flow数据，包含edges和nodes';
COMMENT ON COLUMN "playbook_version"."creator_id" IS '创建人';
COMMENT ON COLUMN "playbook_version"."updator_id" IS '更新人';
COMMENT ON TABLE "playbook_version" IS '剧本版本模型';
        CREATE TABLE "playbook_tag" (
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "tag_id" VARCHAR(36) NOT NULL REFERENCES "tag" ("id") ON DELETE CASCADE
);
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_playbook_name_1a146f" ON "playbook" ("name", "deleted");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_playbook_name_207539" ON "playbook" ("name");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_playbook_name_207539";
        DROP INDEX IF EXISTS "uid_playbook_name_1a146f";
        DROP TABLE IF EXISTS "playbook_tag";
        ALTER TABLE "playbook" ADD "status" INT NOT NULL DEFAULT 0;
        ALTER TABLE "playbook" ADD "version" VARCHAR(30) NOT NULL DEFAULT 0;
        ALTER TABLE "playbook" ADD "creator_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "playbook" ADD "updator_id" VARCHAR(36) NOT NULL;
        ALTER TABLE "playbook" ADD "flow_json" JSONB NOT NULL;
        ALTER TABLE "playbook" ADD "tags" JSONB NOT NULL;
        DROP TABLE IF EXISTS "playbook_version";
        ALTER TABLE "playbook" ADD CONSTRAINT "fk_playbook_account_dc41491a" FOREIGN KEY ("updator_id") REFERENCES "account" ("id") ON DELETE CASCADE;
        ALTER TABLE "playbook" ADD CONSTRAINT "fk_playbook_account_48c30a25" FOREIGN KEY ("creator_id") REFERENCES "account" ("id") ON DELETE CASCADE;
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_playbook_name_7d99d8" ON "playbook" ("name", "version");"""
