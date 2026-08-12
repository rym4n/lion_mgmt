from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

MENU_ID = "c3f1a7e2-5b84-4d69-9f02-1e7ab6d84c31"


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "llm_model_config" (
            "id" VARCHAR(36) NOT NULL PRIMARY KEY,
            "ctime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "utime" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "deleted" BOOL NOT NULL DEFAULT False,
            "tenant_id" VARCHAR(255) DEFAULT '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            "name" VARCHAR(128) NOT NULL,
            "base_url" VARCHAR(512) NOT NULL,
            "api_key" VARCHAR(2048) NOT NULL,
            "model_name" VARCHAR(128) NOT NULL,
            "purpose" VARCHAR(32),
            "enabled" BOOL NOT NULL DEFAULT True,
            "remark" VARCHAR(512),
            CONSTRAINT "uid_llm_model_c_tenant_name" UNIQUE ("tenant_id", "name")
        );
        COMMENT ON COLUMN "llm_model_config"."name" IS '配置名称，如「宏云-aioe」';
        COMMENT ON COLUMN "llm_model_config"."base_url" IS 'OpenAI 兼容端点';
        COMMENT ON COLUMN "llm_model_config"."api_key" IS 'API Key，加密存储';
        COMMENT ON COLUMN "llm_model_config"."model_name" IS '模型名，如 aioe';
        COMMENT ON COLUMN "llm_model_config"."purpose" IS '用途：chat 主力 | lite 轻量 | null 仅员工指定';
        COMMENT ON COLUMN "llm_model_config"."enabled" IS '是否启用';
        COMMENT ON COLUMN "llm_model_config"."remark" IS '备注';

        ALTER TABLE "command_center_agent" ADD COLUMN "llm_config_id" VARCHAR(36);
        COMMENT ON COLUMN "command_center_agent"."llm_config_id" IS '指定模型配置，为空则用全局默认';
        ALTER TABLE "command_center_agent"
            ADD CONSTRAINT "fk_cmd_agent_llm_config"
            FOREIGN KEY ("llm_config_id") REFERENCES "llm_model_config" ("id") ON DELETE SET NULL;

        INSERT INTO "public"."permissions_menu"
            ("id", "ctime", "utime", "deleted", "model", "description", "Permissions", "bp", "url")
        VALUES (
            'c3f1a7e2-5b84-4d69-9f02-1e7ab6d84c31',
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'f',
            'ai_config', '模型配置',
            '[{"label": "新增模型配置权限", "value": "c"}, {"label": "删除模型配置权限", "value": "d"}, {"label": "修改模型配置权限", "value": "u"}, {"label": "查看模型配置权限", "value": "r"}]',
            'ai_bp',
            '{"CN":{"meta": {"icon": "material-symbols-light:settings", "rank": 99, "title": "系统设置", "requireRoot": true}, "path": "/System", "children": [{"meta": {"icon": "mingcute:ai-line", "title": "模型配置"}, "name": "ModelConfig", "path": "/System/ModelConfig/index", "component": "views/System/ModelConfig/index.vue"}]},"HK":{"meta": {"icon": "material-symbols-light:settings", "rank": 99, "title": "系統設置", "requireRoot": true}, "path": "/System", "children": [{"meta": {"icon": "mingcute:ai-line", "title": "模型配置"}, "name": "ModelConfig", "path": "/System/ModelConfig/index", "component": "views/System/ModelConfig/index.vue"}]},"EN":{"meta": {"icon": "material-symbols-light:settings", "rank": 99, "title": "System Settings", "requireRoot": true}, "path": "/System", "children": [{"meta": {"icon": "mingcute:ai-line", "title": "Model Config"}, "name": "ModelConfig", "path": "/System/ModelConfig/index", "component": "views/System/ModelConfig/index.vue"}]}}'
        ) ON CONFLICT ("id") DO UPDATE SET
            "model" = EXCLUDED."model", "description" = EXCLUDED."description",
            "Permissions" = EXCLUDED."Permissions", "bp" = EXCLUDED."bp", "url" = EXCLUDED."url";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "public"."permissions_menu" WHERE "id" = 'c3f1a7e2-5b84-4d69-9f02-1e7ab6d84c31';
        ALTER TABLE "command_center_agent" DROP CONSTRAINT IF EXISTS "fk_cmd_agent_llm_config";
        ALTER TABLE "command_center_agent" DROP COLUMN IF EXISTS "llm_config_id";
        DROP TABLE IF EXISTS "llm_model_config";
    """
