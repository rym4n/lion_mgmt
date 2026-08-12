from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return r"""
        UPDATE "supply_chain_ci_model_group"
        SET "is_locked" = FALSE
        WHERE "is_locked" = TRUE;

        UPDATE "supply_chain_ci_type"
        SET "is_locked" = FALSE
        WHERE "is_locked" = TRUE;

        UPDATE "supply_chain_ci_field_def"
        SET "is_locked" = FALSE
        WHERE "is_locked" = TRUE;

        UPDATE "supply_chain_ci_unique_rule"
        SET "is_locked" = FALSE
        WHERE "is_locked" = TRUE;

        UPDATE "supply_chain_ci_relation_type"
        SET "is_locked" = FALSE
        WHERE "is_locked" = TRUE;

        WITH replacements AS (
            SELECT
                custom."id" AS custom_group_id,
                replacement."id" AS replacement_group_id,
                replacement."key" AS replacement_group_key
            FROM "supply_chain_ci_model_group" AS custom
            JOIN LATERAL (
                SELECT candidate."id", candidate."key"
                FROM "supply_chain_ci_model_group" AS candidate
                WHERE candidate."tenant_id" IS NOT DISTINCT FROM custom."tenant_id"
                  AND candidate."deleted" = FALSE
                  AND candidate."key" <> 'custom'
                ORDER BY candidate."is_builtin" DESC, candidate."sort", candidate."key"
                LIMIT 1
            ) AS replacement ON TRUE
            WHERE custom."deleted" = FALSE
              AND custom."key" = 'custom'
        )
        UPDATE "supply_chain_ci_type" AS model
        SET
            "group_id" = replacements.replacement_group_id,
            "group_key" = replacements.replacement_group_key,
            "utime" = CURRENT_TIMESTAMP
        FROM replacements
        WHERE model."group_id" = replacements.custom_group_id;

        UPDATE "supply_chain_ci_model_group" AS custom
        SET
            "deleted" = TRUE,
            "utime" = CURRENT_TIMESTAMP
        WHERE custom."deleted" = FALSE
          AND custom."key" = 'custom'
          AND NOT EXISTS (
              SELECT 1
              FROM "supply_chain_ci_type" AS model
              WHERE model."group_id" = custom."id"
                AND model."deleted" = FALSE
          );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return ""
