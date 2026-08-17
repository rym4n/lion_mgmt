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

        -- §3. 菜单入口：不在此处插入。
        -- 「模型配置」菜单已存在于 permissions_menu（model='ai_config'，bp='ai_bp'），
        -- 由既有 seed 建立；本系统没有 menu_config 表。
        -- 「仅 default 租户可见」由 services/license/views.py 的 hidden_menus
        -- 在下发时过滤承担（契约 D8），不靠迁移改数据。
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- 回滚不支持数据迁移（租户收敛不可逆），只删除列
        ALTER TABLE "llm_model_config" DROP COLUMN "dimensions";
    """
