from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "network_topology"
            ALTER COLUMN "source_config" SET DEFAULT '',
            ADD COLUMN "action_sources" JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN "refresh_interval" INT NOT NULL DEFAULT 3600,
            ADD COLUMN "enabled" BOOL NOT NULL DEFAULT TRUE,
            ADD COLUMN "executor_id" VARCHAR(36),
            ADD COLUMN "source_hash" VARCHAR(64),
            ADD COLUMN "revision" INT NOT NULL DEFAULT 1,
            ADD COLUMN "last_run_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN "refresh_status" VARCHAR(32) NOT NULL DEFAULT 'pending',
            ADD COLUMN "last_refresh_at" TIMESTAMPTZ,
            ADD COLUMN "next_refresh_at" TIMESTAMPTZ,
            ADD COLUMN "refresh_started_at" TIMESTAMPTZ,
            ADD COLUMN "refresh_token" VARCHAR(36),
            ADD COLUMN "refresh_error" TEXT;

        UPDATE "network_topology"
        SET
            "enabled" = FALSE,
            "refresh_status" = 'legacy',
            "next_refresh_at" = NULL
        WHERE COALESCE(jsonb_array_length("action_sources"), 0) = 0;

        UPDATE "permissions_menu"
        SET
            "Permissions" = '["network_topology:c","network_topology:r","network_topology:u","network_topology:d"]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'network-management-topology';

        UPDATE "role" AS role_row
        SET
            "permissions" = (
                SELECT COALESCE(jsonb_agg(DISTINCT permission_value ORDER BY permission_value), '[]'::jsonb)
                FROM (
                    SELECT permission_value
                    FROM jsonb_array_elements_text(COALESCE(role_row."permissions", '[]'::jsonb)) AS existing(permission_value)
                    WHERE permission_value <> 'network_topology:w'
                    UNION
                    SELECT permission_value
                    FROM jsonb_array_elements_text(
                        '["network_topology:c","network_topology:u","network_topology:d"]'::jsonb
                    ) AS replacement(permission_value)
                ) AS migrated
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE COALESCE(role_row."permissions", '[]'::jsonb) ? 'network_topology:w';

        COMMENT ON COLUMN "network_topology"."action_sources" IS '应用动作数据源绑定';
        COMMENT ON COLUMN "network_topology"."source_config" IS '动作采集的设备配置（应用层加密存储）';
        COMMENT ON COLUMN "network_topology"."refresh_interval" IS '自动刷新周期（秒）';
        COMMENT ON COLUMN "network_topology"."enabled" IS '是否启用自动刷新';
        COMMENT ON COLUMN "network_topology"."executor_id" IS '自动采集执行账户ID';
        COMMENT ON COLUMN "network_topology"."source_hash" IS '最近成功配置内容哈希';
        COMMENT ON COLUMN "network_topology"."revision" IS '数据源定义版本';
        COMMENT ON COLUMN "network_topology"."last_run_ids" IS '最近一次采集运行ID';
        COMMENT ON COLUMN "network_topology"."refresh_status" IS '刷新状态';
        COMMENT ON COLUMN "network_topology"."last_refresh_at" IS '最近一次刷新完成时间';
        COMMENT ON COLUMN "network_topology"."next_refresh_at" IS '下次刷新时间';
        COMMENT ON COLUMN "network_topology"."refresh_started_at" IS '本次刷新开始时间';
        COMMENT ON COLUMN "network_topology"."refresh_token" IS '刷新租约令牌';
        COMMENT ON COLUMN "network_topology"."refresh_error" IS '最近一次刷新错误';
        COMMENT ON TABLE "network_topology" IS '由应用动作采集设备配置后自动生成的网络拓扑';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET
            "Permissions" = '["network_topology:r","network_topology:w"]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'network-management-topology';

        UPDATE "role" AS role_row
        SET
            "permissions" = (
                SELECT COALESCE(jsonb_agg(DISTINCT permission_value ORDER BY permission_value), '[]'::jsonb)
                FROM (
                    SELECT permission_value
                    FROM jsonb_array_elements_text(COALESCE(role_row."permissions", '[]'::jsonb)) AS existing(permission_value)
                    WHERE permission_value NOT IN (
                        'network_topology:c',
                        'network_topology:u',
                        'network_topology:d'
                    )
                    UNION
                    SELECT 'network_topology:w'
                    WHERE COALESCE(role_row."permissions", '[]'::jsonb) ?| ARRAY[
                        'network_topology:c',
                        'network_topology:u',
                        'network_topology:d'
                    ]
                ) AS migrated
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE COALESCE(role_row."permissions", '[]'::jsonb) ?| ARRAY[
            'network_topology:c',
            'network_topology:u',
            'network_topology:d'
        ];

        ALTER TABLE "network_topology"
            DROP COLUMN "refresh_error",
            DROP COLUMN "refresh_token",
            DROP COLUMN "refresh_started_at",
            DROP COLUMN "next_refresh_at",
            DROP COLUMN "last_refresh_at",
            DROP COLUMN "refresh_status",
            DROP COLUMN "last_run_ids",
            DROP COLUMN "revision",
            DROP COLUMN "source_hash",
            DROP COLUMN "executor_id",
            DROP COLUMN "enabled",
            DROP COLUMN "refresh_interval",
            DROP COLUMN "action_sources";
    """
