from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "role" AS r
        SET
            "permissions" = (
                SELECT COALESCE(jsonb_agg(DISTINCT permission_value ORDER BY permission_value), '[]'::jsonb)
                FROM (
                    SELECT permission_value
                    FROM jsonb_array_elements_text(COALESCE(r."permissions", '[]'::jsonb)) AS existing(permission_value)
                    UNION
                    SELECT 'tool:resource:uncontrolled' AS permission_value
                ) AS merged
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE r."permissions" IS NOT NULL
          AND COALESCE(r."permissions", '[]'::jsonb) ? 'tool:r'
          AND NOT (COALESCE(r."permissions", '[]'::jsonb) ? 'tool:resource:uncontrolled');
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        SELECT 1;
    """
