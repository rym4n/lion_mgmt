from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return r"""
        CREATE TEMP TABLE "tmp_builtin_employee_models" ON COMMIT DROP AS
        SELECT "id", "tenant_id"
        FROM "supply_chain_ci_type"
        WHERE "key" = 'user'
          AND "is_builtin" = TRUE
          AND "owner" = 'product';

        UPDATE "supply_chain_ci" AS ci
        SET
            "attributes" = (
                COALESCE(ci."attributes", '{}'::jsonb)
                    - ARRAY['inst_name', 'username']::text[]
            )
            || CASE
                WHEN NOT (
                    COALESCE(ci."attributes", '{}'::jsonb) ? 'person_name'
                )
                 AND NULLIF(ci."attributes" ->> 'inst_name', '') IS NOT NULL
                THEN JSONB_BUILD_OBJECT(
                    'person_name', ci."attributes" -> 'inst_name'
                )
                ELSE '{}'::jsonb
            END
            || CASE
                WHEN NOT (
                    COALESCE(ci."attributes", '{}'::jsonb) ? 'employee_no'
                )
                 AND NULLIF(ci."attributes" ->> 'username', '') IS NOT NULL
                THEN JSONB_BUILD_OBJECT(
                    'employee_no', ci."attributes" -> 'username'
                )
                ELSE '{}'::jsonb
            END,
            "utime" = CURRENT_TIMESTAMP
        WHERE EXISTS (
            SELECT 1
            FROM "tmp_builtin_employee_models" AS model
            WHERE model."tenant_id" IS NOT DISTINCT FROM ci."tenant_id"
              AND model."id" = ci."ci_type_id"
        )
          AND (
              COALESCE(ci."attributes", '{}'::jsonb) ? 'inst_name'
              OR COALESCE(ci."attributes", '{}'::jsonb) ? 'username'
          );

        UPDATE "supply_chain_ci_field_def" AS field
        SET
            "deleted" = TRUE,
            "utime" = CURRENT_TIMESTAMP
        WHERE field."is_builtin" = TRUE
          AND field."builtin_key" IN ('user.inst_name', 'user.username')
          AND EXISTS (
              SELECT 1
              FROM "tmp_builtin_employee_models" AS model
              WHERE model."tenant_id" IS NOT DISTINCT FROM field."tenant_id"
                AND model."id" = field."ci_type_id"
          );

        UPDATE "supply_chain_ci_type" AS model
        SET
            "name" = '员工',
            "icon" = 'ri:contacts-book-line',
            "primary_field" = 'person_name',
            "sort" = 235,
            "builtin_hash" =
                'd5c1b427551f71bd6b1b847e00933ef25e609575c1eb0140ced49ff07fe570e5',
            "deleted" = FALSE,
            "utime" = CURRENT_TIMESTAMP
        WHERE EXISTS (
            SELECT 1
            FROM "tmp_builtin_employee_models" AS employee
            WHERE employee."id" = model."id"
        );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return ""
