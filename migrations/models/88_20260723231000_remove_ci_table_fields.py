from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return r"""
        CREATE TEMP TABLE "tmp_removed_ci_table_fields" ON COMMIT DROP AS
        SELECT
            field."id",
            field."tenant_id",
            field."ci_type_id",
            field."field_key"
        FROM "supply_chain_ci_field_def" AS field
        WHERE field."deleted" = FALSE
          AND field."data_type" = 'table';

        CREATE INDEX "idx_tmp_removed_ci_table_fields_scope"
            ON "tmp_removed_ci_table_fields" ("tenant_id", "ci_type_id");

        UPDATE "supply_chain_ci" AS ci
        SET
            "attributes" = COALESCE(ci."attributes", '{}'::jsonb) - COALESCE(
                (
                    SELECT ARRAY_AGG(field."field_key")
                    FROM "tmp_removed_ci_table_fields" AS field
                    WHERE field."tenant_id" IS NOT DISTINCT FROM ci."tenant_id"
                      AND field."ci_type_id" = ci."ci_type_id"
                ),
                ARRAY[]::text[]
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE EXISTS (
            SELECT 1
            FROM "tmp_removed_ci_table_fields" AS field
            WHERE field."tenant_id" IS NOT DISTINCT FROM ci."tenant_id"
              AND field."ci_type_id" = ci."ci_type_id"
              AND ci."attributes" ? field."field_key"
        );

        UPDATE "supply_chain_ci_type" AS ci_type
        SET
            "primary_field" = '',
            "utime" = CURRENT_TIMESTAMP
        WHERE EXISTS (
            SELECT 1
            FROM "tmp_removed_ci_table_fields" AS field
            WHERE field."tenant_id" IS NOT DISTINCT FROM ci_type."tenant_id"
              AND field."ci_type_id" = ci_type."id"
              AND field."field_key" = ci_type."primary_field"
        );

        CREATE TEMP TABLE "tmp_table_field_unique_rules" ON COMMIT DROP AS
        SELECT DISTINCT unique_rule."id"
        FROM "supply_chain_ci_unique_rule" AS unique_rule
        JOIN "tmp_removed_ci_table_fields" AS field
          ON field."tenant_id" IS NOT DISTINCT FROM unique_rule."tenant_id"
         AND field."ci_type_id" = unique_rule."ci_type_id"
         AND unique_rule."field_keys" ? field."field_key"
        WHERE unique_rule."deleted" = FALSE;

        UPDATE "supply_chain_ci_unique_rule" AS unique_rule
        SET
            "field_keys" = (
                SELECT COALESCE(
                    JSONB_AGG(field_key.value ORDER BY field_key.position),
                    '[]'::jsonb
                )
                FROM JSONB_ARRAY_ELEMENTS_TEXT(unique_rule."field_keys")
                    WITH ORDINALITY AS field_key(value, position)
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM "tmp_removed_ci_table_fields" AS removed
                    WHERE removed."tenant_id"
                            IS NOT DISTINCT FROM unique_rule."tenant_id"
                      AND removed."ci_type_id" = unique_rule."ci_type_id"
                      AND removed."field_key" = field_key.value
                )
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE unique_rule."id" IN (
            SELECT affected."id" FROM "tmp_table_field_unique_rules" AS affected
        );

        UPDATE "supply_chain_ci_unique_rule" AS unique_rule
        SET
            "deleted" = TRUE,
            "utime" = CURRENT_TIMESTAMP
        WHERE unique_rule."id" IN (
            SELECT affected."id" FROM "tmp_table_field_unique_rules" AS affected
        )
          AND JSONB_ARRAY_LENGTH(unique_rule."field_keys") = 0;

        CREATE TEMP TABLE "tmp_removed_relation_table_fields" ON COMMIT DROP AS
        SELECT
            relation_type."tenant_id",
            relation_type."key" AS relation_type_key,
            field.value ->> 'field_key' AS field_key
        FROM "supply_chain_ci_relation_type" AS relation_type
        CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS(
            COALESCE(relation_type."relation_fields", '[]'::jsonb)
        ) AS field(value)
        WHERE relation_type."deleted" = FALSE
          AND field.value ->> 'data_type' = 'table';

        CREATE INDEX "idx_tmp_removed_relation_table_fields_scope"
            ON "tmp_removed_relation_table_fields" ("tenant_id", relation_type_key);

        UPDATE "supply_chain_ci_relation" AS relation
        SET
            "attributes" = COALESCE(relation."attributes", '{}'::jsonb) - COALESCE(
                (
                    SELECT ARRAY_AGG(field.field_key)
                    FROM "tmp_removed_relation_table_fields" AS field
                    WHERE field."tenant_id" IS NOT DISTINCT FROM relation."tenant_id"
                      AND field.relation_type_key = relation."relation_type_key"
                      AND COALESCE(field.field_key, '') <> ''
                ),
                ARRAY[]::text[]
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE EXISTS (
            SELECT 1
            FROM "tmp_removed_relation_table_fields" AS field
            WHERE field."tenant_id" IS NOT DISTINCT FROM relation."tenant_id"
              AND field.relation_type_key = relation."relation_type_key"
              AND COALESCE(field.field_key, '') <> ''
              AND relation."attributes" ? field.field_key
        );

        UPDATE "supply_chain_ci_relation_type" AS relation_type
        SET
            "relation_fields" = COALESCE(
                (
                    SELECT JSONB_AGG(field.value ORDER BY field.position)
                    FROM JSONB_ARRAY_ELEMENTS(relation_type."relation_fields")
                        WITH ORDINALITY AS field(value, position)
                    WHERE field.value ->> 'data_type' IS DISTINCT FROM 'table'
                ),
                '[]'::jsonb
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE EXISTS (
            SELECT 1
            FROM "tmp_removed_relation_table_fields" AS removed
            WHERE removed."tenant_id" IS NOT DISTINCT FROM relation_type."tenant_id"
              AND removed.relation_type_key = relation_type."key"
        );

        UPDATE "supply_chain_ci_field_def"
        SET
            "default_value" = NULL,
            "utime" = CURRENT_TIMESTAMP
        WHERE "data_type" = 'ref'
          AND "default_value" IS NOT NULL;

        UPDATE "supply_chain_ci_field_def" AS field
        SET
            "deleted" = TRUE,
            "utime" = CURRENT_TIMESTAMP
        FROM "tmp_removed_ci_table_fields" AS removed
        WHERE field."id" = removed."id";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return ""
