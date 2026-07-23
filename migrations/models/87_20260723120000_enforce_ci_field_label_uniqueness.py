from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return r"""
        CREATE TEMP TABLE "tmp_ci_field_removals" ON COMMIT DROP AS
        WITH ranked_fields AS (
            SELECT
                field."id",
                field."tenant_id",
                field."ci_type_id",
                field."field_key",
                field."data_type",
                field."sort",
                field."is_builtin",
                field."is_derived",
                FIRST_VALUE(field."id") OVER field_name AS canonical_id,
                ROW_NUMBER() OVER field_name AS occurrence
            FROM "supply_chain_ci_field_def" AS field
            WHERE field."deleted" = FALSE
            WINDOW field_name AS (
                PARTITION BY
                    COALESCE(field."tenant_id", ''),
                    field."ci_type_id",
                    LOWER(REGEXP_REPLACE(BTRIM(field."label"), '\s+', ' ', 'g'))
                ORDER BY
                    field."is_derived",
                    field."sort",
                    field."ctime",
                    field."id"
            )
        )
        SELECT
            redundant."id" AS redundant_id,
            redundant."tenant_id",
            redundant."ci_type_id",
            redundant."field_key" AS redundant_field_key,
            redundant."data_type" AS redundant_data_type,
            redundant."sort" AS redundant_sort,
            CASE
                WHEN redundant.occurrence > 1
                 AND NOT (
                     canonical."is_builtin" = TRUE
                     AND canonical."is_derived" = TRUE
                 )
                    THEN canonical."field_key"
            END AS canonical_field_key,
            CASE
                WHEN redundant.occurrence > 1
                 AND NOT (
                     canonical."is_builtin" = TRUE
                     AND canonical."is_derived" = TRUE
                 )
                    THEN canonical."data_type"
            END AS canonical_data_type
        FROM ranked_fields AS redundant
        LEFT JOIN ranked_fields AS canonical
          ON canonical."id" = redundant.canonical_id
        WHERE redundant.occurrence > 1
           OR (
                redundant."is_builtin" = TRUE
                AND redundant."is_derived" = TRUE
           );

        CREATE UNIQUE INDEX "uid_tmp_ci_field_removals_id"
            ON "tmp_ci_field_removals" (redundant_id);
        CREATE INDEX "idx_tmp_ci_field_removals_scope"
            ON "tmp_ci_field_removals" ("tenant_id", "ci_type_id");

        UPDATE "supply_chain_ci" AS ci
        SET
            "attributes" = (
                COALESCE(ci."attributes", '{}'::jsonb)
                - COALESCE(
                    (
                        SELECT ARRAY_AGG(removal.redundant_field_key)
                        FROM "tmp_ci_field_removals" AS removal
                        WHERE removal."tenant_id" IS NOT DISTINCT FROM ci."tenant_id"
                          AND removal."ci_type_id" = ci."ci_type_id"
                    ),
                    ARRAY[]::text[]
                )
            ) || COALESCE(
                (
                    SELECT JSONB_OBJECT_AGG(
                        transferable.canonical_field_key,
                        transferable.field_value
                    )
                    FROM (
                        SELECT DISTINCT ON (removal.canonical_field_key)
                            removal.canonical_field_key,
                            ci."attributes" -> removal.redundant_field_key
                                AS field_value
                        FROM "tmp_ci_field_removals" AS removal
                        WHERE removal."tenant_id" IS NOT DISTINCT FROM ci."tenant_id"
                          AND removal."ci_type_id" = ci."ci_type_id"
                          AND removal.canonical_field_key IS NOT NULL
                          AND removal.redundant_data_type = removal.canonical_data_type
                          AND ci."attributes" ? removal.redundant_field_key
                          AND ci."attributes" -> removal.redundant_field_key
                              IS DISTINCT FROM 'null'::jsonb
                          AND (
                              NOT ci."attributes" ? removal.canonical_field_key
                              OR ci."attributes" -> removal.canonical_field_key
                                  = 'null'::jsonb
                          )
                        ORDER BY
                            removal.canonical_field_key,
                            removal.redundant_sort,
                            removal.redundant_id
                    ) AS transferable
                ),
                '{}'::jsonb
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE EXISTS (
            SELECT 1
            FROM "tmp_ci_field_removals" AS removal
            WHERE removal."tenant_id" IS NOT DISTINCT FROM ci."tenant_id"
              AND removal."ci_type_id" = ci."ci_type_id"
              AND ci."attributes" ? removal.redundant_field_key
        );

        UPDATE "supply_chain_ci_type" AS ci_type
        SET
            "primary_field" = removal.canonical_field_key,
            "utime" = CURRENT_TIMESTAMP
        FROM "tmp_ci_field_removals" AS removal
        WHERE removal.canonical_field_key IS NOT NULL
          AND removal."tenant_id" IS NOT DISTINCT FROM ci_type."tenant_id"
          AND removal."ci_type_id" = ci_type."id"
          AND removal.redundant_field_key = ci_type."primary_field";

        UPDATE "supply_chain_ci_type" AS ci_type
        SET
            "primary_field" = COALESCE(
                (
                    SELECT candidate."field_key"
                    FROM "supply_chain_ci_field_def" AS candidate
                    WHERE candidate."tenant_id" IS NOT DISTINCT FROM ci_type."tenant_id"
                      AND candidate."ci_type_id" = ci_type."id"
                      AND candidate."deleted" = FALSE
                      AND candidate."data_type" = 'text'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM "tmp_ci_field_removals" AS removed_candidate
                          WHERE removed_candidate.redundant_id = candidate."id"
                      )
                    ORDER BY
                        candidate."field_key" <> 'inst_name',
                        candidate."sort",
                        candidate."ctime",
                        candidate."id"
                    LIMIT 1
                ),
                ''
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE EXISTS (
            SELECT 1
            FROM "tmp_ci_field_removals" AS removal
            WHERE removal.canonical_field_key IS NULL
              AND removal."tenant_id" IS NOT DISTINCT FROM ci_type."tenant_id"
              AND removal."ci_type_id" = ci_type."id"
              AND removal.redundant_field_key = ci_type."primary_field"
        );

        UPDATE "supply_chain_ci_unique_rule" AS unique_rule
        SET
            "field_keys" = (
                SELECT COALESCE(
                    JSONB_AGG(deduplicated.resolved_key ORDER BY deduplicated.first_position),
                    '[]'::jsonb
                )
                FROM (
                    SELECT
                        mapped.resolved_key,
                        MIN(mapped.position) AS first_position
                    FROM (
                        SELECT
                            CASE
                                WHEN removal.redundant_id IS NULL THEN field_key.value
                                ELSE removal.canonical_field_key
                            END AS resolved_key,
                            field_key.position
                        FROM JSONB_ARRAY_ELEMENTS_TEXT(unique_rule."field_keys")
                            WITH ORDINALITY AS field_key(value, position)
                        LEFT JOIN "tmp_ci_field_removals" AS removal
                          ON removal."tenant_id" IS NOT DISTINCT FROM unique_rule."tenant_id"
                         AND removal."ci_type_id" = unique_rule."ci_type_id"
                         AND removal.redundant_field_key = field_key.value
                    ) AS mapped
                    WHERE mapped.resolved_key IS NOT NULL
                    GROUP BY mapped.resolved_key
                ) AS deduplicated
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE EXISTS (
            SELECT 1
            FROM "tmp_ci_field_removals" AS removal
            WHERE removal."tenant_id" IS NOT DISTINCT FROM unique_rule."tenant_id"
              AND removal."ci_type_id" = unique_rule."ci_type_id"
              AND unique_rule."field_keys" ? removal.redundant_field_key
        );

        UPDATE "supply_chain_ci_field_def" AS field
        SET
            "deleted" = TRUE,
            "utime" = CURRENT_TIMESTAMP
        FROM "tmp_ci_field_removals" AS removal
        WHERE field."id" = removal.redundant_id;

        CREATE UNIQUE INDEX IF NOT EXISTS "uid_ci_field_tenant_type_label"
            ON "supply_chain_ci_field_def" (
                COALESCE("tenant_id", ''),
                "ci_type_id",
                LOWER(REGEXP_REPLACE(BTRIM("label"), '\s+', ' ', 'g'))
            )
            WHERE "deleted" = FALSE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return 'DROP INDEX IF EXISTS "uid_ci_field_tenant_type_label";'
