from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "supply_chain_ci_type"
            ADD COLUMN "sort" INT NOT NULL DEFAULT 0;

        WITH ranked_models AS (
            SELECT
                "id",
                (
                    ROW_NUMBER() OVER (
                        PARTITION BY "tenant_id"
                        ORDER BY "deleted", "key", "ctime", "id"
                    )
                )::INT AS position
            FROM "supply_chain_ci_type"
        )
        UPDATE "supply_chain_ci_type" AS model
        SET "sort" = ranked_models.position
        FROM ranked_models
        WHERE model."id" = ranked_models."id";

        CREATE INDEX IF NOT EXISTS "idx_supply_chain_ci_type_tenant_sort"
            ON "supply_chain_ci_type" ("tenant_id", "sort");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_supply_chain_ci_type_tenant_sort";
        ALTER TABLE "supply_chain_ci_type" DROP COLUMN "sort";
    """
