from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "network_topology"
            RENAME COLUMN "last_refresh_at" TO "last_sync_at";

        ALTER TABLE "network_topology"
            DROP COLUMN "action_sources",
            DROP COLUMN "refresh_interval",
            DROP COLUMN "enabled",
            DROP COLUMN "executor_id",
            DROP COLUMN "last_run_ids",
            DROP COLUMN "refresh_status",
            DROP COLUMN "next_refresh_at",
            DROP COLUMN "refresh_started_at",
            DROP COLUMN "refresh_token",
            DROP COLUMN "refresh_error";

        COMMENT ON COLUMN "network_topology"."source_config" IS
            '外部剧本最近一次写入的设备配置（应用层加密存储）';
        COMMENT ON COLUMN "network_topology"."source_hash" IS
            '最近一次写入的设备配置内容哈希';
        COMMENT ON COLUMN "network_topology"."revision" IS '拓扑数据版本';
        COMMENT ON COLUMN "network_topology"."last_sync_at" IS
            '最近一次外部配置同步完成时间';
        COMMENT ON TABLE "network_topology" IS
            '网络拓扑数据；自动采集和定时调度由外部剧本负责';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "network_topology"
            ADD COLUMN "action_sources" JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN "refresh_interval" INT NOT NULL DEFAULT 3600,
            ADD COLUMN "enabled" BOOL NOT NULL DEFAULT TRUE,
            ADD COLUMN "executor_id" VARCHAR(36),
            ADD COLUMN "last_run_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN "refresh_status" VARCHAR(32) NOT NULL DEFAULT 'pending',
            ADD COLUMN "next_refresh_at" TIMESTAMPTZ,
            ADD COLUMN "refresh_started_at" TIMESTAMPTZ,
            ADD COLUMN "refresh_token" VARCHAR(36),
            ADD COLUMN "refresh_error" TEXT;

        ALTER TABLE "network_topology"
            RENAME COLUMN "last_sync_at" TO "last_refresh_at";
    """
