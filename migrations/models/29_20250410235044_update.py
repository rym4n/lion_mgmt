from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "playbook" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "remark" TEXT NOT NULL,
    "status" INT NOT NULL,
    "start_app" VARCHAR(36) NOT NULL,
    "end_app" VARCHAR(36) NOT NULL,
    "if_list" TEXT NOT NULL,
    "audit_list" TEXT NOT NULL,
    "tags" JSONB NOT NULL,
    "flow_json" JSONB NOT NULL,
    "flow_data" JSONB NOT NULL,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "playbook"."ctime" IS '创建时间';
COMMENT ON COLUMN "playbook"."utime" IS '更新时间';
COMMENT ON COLUMN "playbook"."deleted" IS '删除标识';
COMMENT ON COLUMN "playbook"."name" IS '剧本名称';
COMMENT ON COLUMN "playbook"."remark" IS '描述';
COMMENT ON COLUMN "playbook"."status" IS '剧本状态';
COMMENT ON COLUMN "playbook"."start_app" IS '开始节点';
COMMENT ON COLUMN "playbook"."end_app" IS '结束节点';
COMMENT ON COLUMN "playbook"."if_list" IS '判断节点';
COMMENT ON COLUMN "playbook"."audit_list" IS '审批节点';
COMMENT ON COLUMN "playbook"."tags" IS '标签';
COMMENT ON COLUMN "playbook"."flow_json" IS '流程图布局信息';
COMMENT ON COLUMN "playbook"."flow_data" IS '各节点详细信息';
COMMENT ON COLUMN "playbook"."creator_id" IS '创建人';
COMMENT ON COLUMN "playbook"."updator_id" IS '更新人';
COMMENT ON TABLE "playbook" IS '剧本模型';
        CREATE TABLE IF NOT EXISTS "scene" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "remark" VARCHAR(255) NOT NULL,
    "status" INT NOT NULL,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "scene"."ctime" IS '创建时间';
COMMENT ON COLUMN "scene"."utime" IS '更新时间';
COMMENT ON COLUMN "scene"."deleted" IS '删除标识';
COMMENT ON COLUMN "scene"."name" IS '场景名称';
COMMENT ON COLUMN "scene"."remark" IS '描述';
COMMENT ON COLUMN "scene"."status" IS '是否启用，2启用且为当前场景，1启用，0禁用';
COMMENT ON COLUMN "scene"."creator_id" IS '创建人';
COMMENT ON COLUMN "scene"."updator_id" IS '更新人';
COMMENT ON TABLE "scene" IS '运行场景模型';
        CREATE TABLE "playbook_scene" (
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "scene_id" VARCHAR(36) NOT NULL REFERENCES "scene" ("id") ON DELETE CASCADE
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "playbook";
        DROP TABLE IF EXISTS "scene";"""
