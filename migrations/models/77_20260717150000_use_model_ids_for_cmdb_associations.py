from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return r"""
        ALTER TABLE "supply_chain_ci_field_def"
            ADD COLUMN IF NOT EXISTS "ci_type_id" VARCHAR(36),
            ADD COLUMN IF NOT EXISTS "ref_ci_type_id" VARCHAR(36);
        ALTER TABLE "supply_chain_ci"
            ADD COLUMN IF NOT EXISTS "ci_type_id" VARCHAR(36);
        ALTER TABLE "supply_chain_ci_relation_type"
            ADD COLUMN IF NOT EXISTS "source_ci_type_ids" JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS "target_ci_type_ids" JSONB NOT NULL DEFAULT '[]'::jsonb;

        UPDATE "supply_chain_ci_field_def" field
        SET "ci_type_id" = model."id"
        FROM "supply_chain_ci_type" model
        WHERE model."tenant_id" = field."tenant_id"
          AND model."key" = field."ci_type_key"
          AND field."ci_type_id" IS NULL;

        UPDATE "supply_chain_ci_field_def" field
        SET "ref_ci_type_id" = model."id"
        FROM "supply_chain_ci_type" model
        WHERE model."tenant_id" = field."tenant_id"
          AND model."key" = field."ref_ci_type"
          AND field."ref_ci_type" IS NOT NULL
          AND field."ref_ci_type_id" IS NULL;

        UPDATE "supply_chain_ci" ci
        SET "ci_type_id" = model."id"
        FROM "supply_chain_ci_type" model
        WHERE model."tenant_id" = ci."tenant_id"
          AND model."key" = ci."ci_type_key"
          AND ci."ci_type_id" IS NULL;

        UPDATE "supply_chain_ci_relation_type" relation_type
        SET "source_ci_type_ids" = COALESCE((
                SELECT jsonb_agg(model."id" ORDER BY source.ord)
                FROM jsonb_array_elements_text(relation_type."source_ci_types")
                    WITH ORDINALITY AS source(model_key, ord)
                JOIN "supply_chain_ci_type" model
                  ON model."tenant_id" = relation_type."tenant_id"
                 AND model."key" = source.model_key
            ), '[]'::jsonb),
            "target_ci_type_ids" = COALESCE((
                SELECT jsonb_agg(model."id" ORDER BY target.ord)
                FROM jsonb_array_elements_text(relation_type."target_ci_types")
                    WITH ORDINALITY AS target(model_key, ord)
                JOIN "supply_chain_ci_type" model
                  ON model."tenant_id" = relation_type."tenant_id"
                 AND model."key" = target.model_key
            ), '[]'::jsonb);

        UPDATE "supply_chain_ci_relation_type" relation_type
        SET "relation_fields" = COALESCE((
            SELECT jsonb_agg(
                CASE
                    WHEN field.value ? 'ref_ci_type' THEN
                        field.value || jsonb_build_object(
                            'ref_ci_type_id',
                            (
                                SELECT model."id"
                                FROM "supply_chain_ci_type" model
                                WHERE model."tenant_id" = relation_type."tenant_id"
                                  AND model."key" = field.value->>'ref_ci_type'
                            )
                        )
                    ELSE field.value
                END
                ORDER BY field.ord
            )
            FROM jsonb_array_elements(relation_type."relation_fields")
                WITH ORDINALITY AS field(value, ord)
        ), '[]'::jsonb);

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM "supply_chain_ci_field_def"
                WHERE "ci_type_id" IS NULL
                   OR ("ref_ci_type" IS NOT NULL AND "ref_ci_type_id" IS NULL)
            ) OR EXISTS (
                SELECT 1 FROM "supply_chain_ci" WHERE "ci_type_id" IS NULL
            ) OR EXISTS (
                SELECT 1 FROM "supply_chain_ci_relation_type"
                WHERE jsonb_array_length("source_ci_types") <> jsonb_array_length("source_ci_type_ids")
                   OR jsonb_array_length("target_ci_types") <> jsonb_array_length("target_ci_type_ids")
                   OR EXISTS (
                       SELECT 1 FROM jsonb_array_elements("relation_fields") field
                       WHERE field ? 'ref_ci_type'
                         AND NULLIF(field->>'ref_ci_type_id', '') IS NULL
                   )
            ) THEN
                RAISE EXCEPTION 'CMDB model ID backfill failed: unresolved tenant-scoped model reference';
            END IF;
        END $$;

        ALTER TABLE "supply_chain_ci_field_def"
            ALTER COLUMN "ci_type_id" SET NOT NULL;
        ALTER TABLE "supply_chain_ci"
            ALTER COLUMN "ci_type_id" SET NOT NULL;

        ALTER TABLE "supply_chain_ci_field_def"
            DROP CONSTRAINT IF EXISTS "uid_supply_chain_field_tenant_type_key";
        ALTER TABLE "supply_chain_ci_field_def"
            ADD CONSTRAINT "uid_supply_chain_field_tenant_type_id_key"
            UNIQUE ("tenant_id", "ci_type_id", "field_key");

        CREATE INDEX IF NOT EXISTS "idx_supply_chain_field_tenant_type_id"
            ON "supply_chain_ci_field_def" ("tenant_id", "ci_type_id");
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_ci_tenant_type_id"
            ON "supply_chain_ci" ("tenant_id", "ci_type_id");
        CREATE INDEX IF NOT EXISTS "idx_supply_chain_ci_tenant_type_id_status"
            ON "supply_chain_ci" ("tenant_id", "ci_type_id", "status");

        ALTER TABLE "supply_chain_ci_field_def"
            ADD CONSTRAINT "fk_supply_chain_field_model_id"
            FOREIGN KEY ("ci_type_id") REFERENCES "supply_chain_ci_type" ("id") ON DELETE RESTRICT;
        ALTER TABLE "supply_chain_ci_field_def"
            ADD CONSTRAINT "fk_supply_chain_field_ref_model_id"
            FOREIGN KEY ("ref_ci_type_id") REFERENCES "supply_chain_ci_type" ("id") ON DELETE RESTRICT;
        ALTER TABLE "supply_chain_ci"
            ADD CONSTRAINT "fk_supply_chain_ci_model_id"
            FOREIGN KEY ("ci_type_id") REFERENCES "supply_chain_ci_type" ("id") ON DELETE RESTRICT;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return r"""
        ALTER TABLE "supply_chain_ci_field_def" DROP CONSTRAINT IF EXISTS "fk_supply_chain_field_model_id";
        ALTER TABLE "supply_chain_ci_field_def" DROP CONSTRAINT IF EXISTS "fk_supply_chain_field_ref_model_id";
        ALTER TABLE "supply_chain_ci" DROP CONSTRAINT IF EXISTS "fk_supply_chain_ci_model_id";
        ALTER TABLE "supply_chain_ci_field_def" DROP CONSTRAINT IF EXISTS "uid_supply_chain_field_tenant_type_id_key";
        ALTER TABLE "supply_chain_ci_field_def"
            ADD CONSTRAINT "uid_supply_chain_field_tenant_type_key"
            UNIQUE ("tenant_id", "ci_type_key", "field_key");
        DROP INDEX IF EXISTS "idx_supply_chain_field_tenant_type_id";
        DROP INDEX IF EXISTS "idx_supply_chain_ci_tenant_type_id";
        DROP INDEX IF EXISTS "idx_supply_chain_ci_tenant_type_id_status";
        ALTER TABLE "supply_chain_ci_field_def" DROP COLUMN IF EXISTS "ref_ci_type_id";
        ALTER TABLE "supply_chain_ci_field_def" DROP COLUMN IF EXISTS "ci_type_id";
        ALTER TABLE "supply_chain_ci" DROP COLUMN IF EXISTS "ci_type_id";
        ALTER TABLE "supply_chain_ci_relation_type" DROP COLUMN IF EXISTS "source_ci_type_ids";
        ALTER TABLE "supply_chain_ci_relation_type" DROP COLUMN IF EXISTS "target_ci_type_ids";
    """
