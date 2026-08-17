from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

DEFAULT_TENANT_ID = "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18"


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        -- §1. 添加 dimensions 列（SPEC §5.6）
        ALTER TABLE "llm_model_config" ADD COLUMN "dimensions" INT;
        COMMENT ON COLUMN "llm_model_config"."dimensions" IS '向量维度，仅 embedding 用途填写';

        -- §2. 存量配置迁移到 default 租户（SPEC §5.6 收敛）
        -- 保留 default 租户的配置不动，其他租户的配置迁移到 default
        -- 如果 default 已有同名配置，为非 default 租户的配置改名（追加租户 ID 后缀）
        UPDATE llm_model_config
        SET name = name || '-' || LEFT(tenant_id, 8)
        WHERE tenant_id != '{DEFAULT_TENANT_ID}'
          AND deleted = FALSE
          AND EXISTS (
              SELECT 1 FROM llm_model_config AS existing
              WHERE existing.tenant_id = '{DEFAULT_TENANT_ID}'
                AND existing.name = llm_model_config.name
                AND existing.deleted = FALSE
          );

        -- 将所有非 default 租户的配置迁移到 default 租户
        UPDATE llm_model_config
        SET tenant_id = '{DEFAULT_TENANT_ID}'
        WHERE tenant_id != '{DEFAULT_TENANT_ID}';

        -- §3. 插入模型配置菜单行（如果不存在）
        INSERT INTO menu_config (id, name, path, icon, sort, parent_id, ctime, utime, deleted, tenant_id)
        SELECT
            'llm-config-menu-id'::UUID,
            '模型配置',
            '/settings/llm-config',
            'setting',
            100,
            NULL,
            NOW(),
            NOW(),
            FALSE,
            '{DEFAULT_TENANT_ID}'
        WHERE NOT EXISTS (
            SELECT 1 FROM menu_config
            WHERE path = '/settings/llm-config'
              AND tenant_id = '{DEFAULT_TENANT_ID}'
              AND deleted = FALSE
        );

        -- §4. 清理：软删除非 default 租户的绑定记录不再需要（已迁移）
        -- 注意：不删除 command_agent.llm_config_id 外键，只是所有配置现在都在 default 租户
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- 回滚不支持数据迁移，只删除列
        ALTER TABLE "llm_model_config" DROP COLUMN "dimensions";

        -- 删除菜单行
        DELETE FROM menu_config WHERE path = '/settings/llm-config';
    """
