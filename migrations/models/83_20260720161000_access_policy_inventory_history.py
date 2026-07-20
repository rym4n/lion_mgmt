from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "supply_chain_actual_policy"
            ADD COLUMN "device_key" VARCHAR(255),
            ADD COLUMN "device_name" VARCHAR(255),
            ADD COLUMN "device_type" VARCHAR(30),
            ADD COLUMN "policy_key" VARCHAR(255),
            ADD COLUMN "policy_type" VARCHAR(50),
            ADD COLUMN "enabled" BOOL NOT NULL DEFAULT TRUE,
            ADD COLUMN "policy_data" JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN "revision" INT NOT NULL DEFAULT 1,
            ADD COLUMN "last_synced_at" TIMESTAMPTZ;

        UPDATE "supply_chain_actual_policy"
        SET
            "device_key" = COALESCE(
                NULLIF("device_ci_id", ''),
                'legacy-device:' || "id"
            ),
            "policy_key" = COALESCE(
                NULLIF("raw_rule_id", ''),
                NULLIF("rule_name", ''),
                'legacy-policy:' || "id"
            ),
            "last_synced_at" = "utime";

        WITH ranked_policy AS (
            SELECT
                "id",
                ROW_NUMBER() OVER (
                    PARTITION BY "tenant_id", "device_key", "policy_key"
                    ORDER BY "utime" DESC, "ctime" DESC, "id" DESC
                ) AS row_number
            FROM "supply_chain_actual_policy"
            WHERE NOT "deleted"
        )
        UPDATE "supply_chain_actual_policy" AS policy
        SET "deleted" = TRUE
        FROM ranked_policy
        WHERE policy."id" = ranked_policy."id"
          AND ranked_policy.row_number > 1;

        ALTER TABLE "supply_chain_actual_policy"
            ALTER COLUMN "device_key" SET NOT NULL,
            ALTER COLUMN "policy_key" SET NOT NULL;

        ALTER TABLE "supply_chain_policy_change"
            ADD COLUMN "device_key" VARCHAR(255),
            ADD COLUMN "actual_policy_id" VARCHAR(36),
            ADD COLUMN "policy_key" VARCHAR(255),
            ADD COLUMN "batch_id" VARCHAR(64),
            ADD COLUMN "revision" INT NOT NULL DEFAULT 1,
            ADD COLUMN "before_data" JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN "after_data" JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN "sync_source" VARCHAR(100);

        UPDATE "supply_chain_policy_change"
        SET
            "device_key" = NULLIF("device_ci_id", ''),
            "policy_key" = NULLIF("policy_name", '');

        CREATE INDEX IF NOT EXISTS "idx_scr_actual_tenant_device_key"
            ON "supply_chain_actual_policy" ("tenant_id", "device_key");
        CREATE UNIQUE INDEX IF NOT EXISTS "idx_scr_actual_tenant_policy_identity"
            ON "supply_chain_actual_policy" (
                "tenant_id", "device_key", "policy_key"
            ) WHERE NOT "deleted";
        CREATE INDEX IF NOT EXISTS "idx_scr_policy_history_tenant_device_key"
            ON "supply_chain_policy_change" ("tenant_id", "device_key");
        CREATE INDEX IF NOT EXISTS "idx_scr_policy_history_tenant_policy"
            ON "supply_chain_policy_change" ("tenant_id", "actual_policy_id");
        CREATE INDEX IF NOT EXISTS "idx_scr_policy_history_tenant_batch"
            ON "supply_chain_policy_change" ("tenant_id", "batch_id");

        COMMENT ON TABLE "supply_chain_actual_policy" IS
            '访问控制当前策略清单，由用户或外部剧本通过 CRUD 接口维护';
        COMMENT ON TABLE "supply_chain_policy_change" IS
            '访问控制策略不可变写入历史';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_scr_policy_history_tenant_batch";
        DROP INDEX IF EXISTS "idx_scr_policy_history_tenant_policy";
        DROP INDEX IF EXISTS "idx_scr_policy_history_tenant_device_key";
        DROP INDEX IF EXISTS "idx_scr_actual_tenant_policy_identity";
        DROP INDEX IF EXISTS "idx_scr_actual_tenant_device_key";

        ALTER TABLE "supply_chain_policy_change"
            DROP COLUMN "sync_source",
            DROP COLUMN "after_data",
            DROP COLUMN "before_data",
            DROP COLUMN "revision",
            DROP COLUMN "batch_id",
            DROP COLUMN "policy_key",
            DROP COLUMN "actual_policy_id",
            DROP COLUMN "device_key";

        ALTER TABLE "supply_chain_actual_policy"
            DROP COLUMN "last_synced_at",
            DROP COLUMN "revision",
            DROP COLUMN "policy_data",
            DROP COLUMN "enabled",
            DROP COLUMN "policy_type",
            DROP COLUMN "policy_key",
            DROP COLUMN "device_type",
            DROP COLUMN "device_name",
            DROP COLUMN "device_key";
    """
