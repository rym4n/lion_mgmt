from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "timed" (
    "id" VARCHAR(36) NOT NULL PRIMARY KEY,
    "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "deleted" BOOL NOT NULL DEFAULT False,
    "name" VARCHAR(255) NOT NULL,
    "type" INT NOT NULL,
    "status" INT NOT NULL,
    "tactics" VARCHAR(50) NOT NULL,
    "result" VARCHAR(50) NOT NULL,
    "success_rate" VARCHAR(10) NOT NULL,
    "enable" INT NOT NULL DEFAULT 1,
    "action_id" VARCHAR(36) NOT NULL REFERENCES "tool_action" ("id") ON DELETE CASCADE,
    "creator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE,
    "playbook_id" VARCHAR(36) NOT NULL REFERENCES "playbook" ("id") ON DELETE CASCADE,
    "updator_id" VARCHAR(36) NOT NULL REFERENCES "account" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "timed"."ctime" IS '创建时间';
COMMENT ON COLUMN "timed"."utime" IS '更新时间';
COMMENT ON COLUMN "timed"."deleted" IS '删除标识';
COMMENT ON COLUMN "timed"."name" IS '任务名称';
COMMENT ON COLUMN "timed"."type" IS '执行类型，0为执行剧本，1为执行动作';
COMMENT ON COLUMN "timed"."status" IS '执行状态，0为单次执行，1为周期执行';
COMMENT ON COLUMN "timed"."tactics" IS '执行策略';
COMMENT ON COLUMN "timed"."result" IS '执行结果';
COMMENT ON COLUMN "timed"."success_rate" IS '成功率';
COMMENT ON COLUMN "timed"."enable" IS '是否启用, 0为禁用，1为启用';
COMMENT ON COLUMN "timed"."action_id" IS '关联工具动作';
COMMENT ON COLUMN "timed"."creator_id" IS '创建人';
COMMENT ON COLUMN "timed"."playbook_id" IS '关联剧本';
COMMENT ON COLUMN "timed"."updator_id" IS '更新人';
COMMENT ON TABLE "timed" IS '运行场景模型';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "timed";"""
