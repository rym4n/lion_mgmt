"""LLM 配置收敛策略修正（补偿迁移）。

105 迁移采用了 UPDATE 策略（迁移到 default 租户），但正确的策略应该是
DELETE 非 default 配置（SPEC §5.6）。本迁移修正该问题：

1. 创建备份表（migration_backup schema）
2. 备份所有非 default 租户的配置
3. 备份员工绑定（JSON 格式）
4. DELETE 所有 tenant_id != default 的配置（触发 SET NULL）
5. fail-closed downgrade（三段校验）
"""
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

DEFAULT_TENANT_ID = "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18"


async def upgrade(db: BaseDBAsyncClient) -> str:
    # 生成备份表后缀
    import datetime
    backup_suffix = datetime.datetime.now().strftime("%Y%m%d")

    return f"""
        -- §1. 创建备份 schema（幂等）
        CREATE SCHEMA IF NOT EXISTS migration_backup;

        -- §2. 备份非 default 租户的配置
        DROP TABLE IF EXISTS migration_backup.llm_model_config_backup_{backup_suffix};
        CREATE TABLE migration_backup.llm_model_config_backup_{backup_suffix} AS
        SELECT * FROM llm_model_config
        WHERE tenant_id != '{DEFAULT_TENANT_ID}' AND deleted = FALSE;

        -- §3. 备份员工绑定关系（JSON 格式）
        DROP TABLE IF EXISTS migration_backup.employee_llm_binding_backup_{backup_suffix};
        CREATE TABLE migration_backup.employee_llm_binding_backup_{backup_suffix} AS
        SELECT
            ca.id as employee_id,
            ca.agent_key,
            ca.name as employee_name,
            ca.tenant_id as employee_tenant_id,
            ca.llm_config_id,
            lmc.name as config_name,
            lmc.tenant_id as config_tenant_id,
            lmc.model_name,
            lmc.purpose
        FROM "command_center_agent" ca
        INNER JOIN llm_model_config lmc ON ca.llm_config_id = lmc.id
        WHERE lmc.tenant_id != '{DEFAULT_TENANT_ID}'
          AND ca.deleted = FALSE
          AND lmc.deleted = FALSE;

        -- §4. DELETE 非 default 租户的配置
        -- 这会触发 command_center_agent.llm_config_id 的 on_delete=SET_NULL
        DELETE FROM llm_model_config
        WHERE tenant_id != '{DEFAULT_TENANT_ID}';

        -- §5. 记录迁移元数据
        COMMENT ON SCHEMA migration_backup IS
            '112 迁移备份：收敛策略从 UPDATE 修正为 DELETE，备份时间 {backup_suffix}';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    """fail-closed 回滚：三段校验 + 数据恢复。

    如果任何校验失败，整个回滚拒绝执行——宁可不回滚，
    也不能静默失败留下半残留状态。
    """
    import datetime
    backup_suffix = datetime.datetime.now().strftime("%Y%m%d")

    return f"""
        -- §1. 校验备份表存在
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'migration_backup'
                AND table_name = 'llm_model_config_backup_{backup_suffix}'
            ) THEN
                RAISE EXCEPTION '备份表不存在，无法回滚 112 迁移';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'migration_backup'
                AND table_name = 'employee_llm_binding_backup_{backup_suffix}'
            ) THEN
                RAISE EXCEPTION '员工绑定备份表不存在，无法回滚 112 迁移';
            END IF;
        END $$;

        -- §2. 恢复非 default 租户的配置
        -- 直接从备份表插入（id 会保留，UUID 不会冲突）
        INSERT INTO llm_model_config
        SELECT * FROM migration_backup.llm_model_config_backup_{backup_suffix}
        ON CONFLICT (id) DO NOTHING;

        -- §3. 恢复员工绑定
        -- 使用备份表中的 employee_id 和 llm_config_id 恢复关系
        UPDATE "command_center_agent" ca
        SET llm_config_id = eb.llm_config_id
        FROM migration_backup.employee_llm_binding_backup_{backup_suffix} eb
        WHERE ca.id = eb.employee_id
          AND ca.llm_config_id IS NULL  -- 只恢复被 SET NULL 的
          AND EXISTS (
              SELECT 1 FROM llm_model_config lmc
              WHERE lmc.id = eb.llm_config_id
          );  -- 确保配置已恢复

        -- §4. 验证恢复完整性
        DO $$
        DECLARE
            backup_count INT;
            restored_count INT;
        BEGIN
            SELECT COUNT(*) INTO backup_count
            FROM migration_backup.llm_model_config_backup_{backup_suffix};

            SELECT COUNT(*) INTO restored_count
            FROM llm_model_config
            WHERE tenant_id != '{DEFAULT_TENANT_ID}';

            IF backup_count != restored_count THEN
                RAISE EXCEPTION '配置恢复不完整：备份 % 条，实际恢复 % 条',
                    backup_count, restored_count;
            END IF;
        END $$;

        -- 注意：不删除备份表，保留审计记录
    """
