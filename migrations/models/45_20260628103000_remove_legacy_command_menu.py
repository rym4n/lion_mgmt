from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "permissions_menu"
        WHERE "model" = 'command'
           OR "bp" = 'command_bp'
           OR "url"::text LIKE '%"/command"%'
           OR "url"::text LIKE '%"/command/index"%';

        INSERT INTO "permissions_menu" (
            "id", "ctime", "utime", "deleted", "tenant_id",
            "model", "description", "Permissions", "url", "bp"
        )
        VALUES (
            'command-center-permissions',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            False,
            '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            'command_center',
            '指挥中心',
            '["command_center:c","command_center:r","command_center:u","command_center:d"]',
            '[]',
            'command_center_bp'
        )
        ON CONFLICT ("model") DO UPDATE SET
            "description" = EXCLUDED."description",
            "Permissions" = EXCLUDED."Permissions",
            "url" = EXCLUDED."url",
            "bp" = EXCLUDED."bp",
            "deleted" = False,
            "utime" = CURRENT_TIMESTAMP;

        UPDATE "role"
        SET "permissions" = (
            SELECT COALESCE(jsonb_agg(DISTINCT mapped_permission), '[]'::jsonb)
            FROM (
                SELECT CASE
                    WHEN permission_value = 'command:c' THEN 'command_center:c'
                    WHEN permission_value = 'command:r' THEN 'command_center:r'
                    WHEN permission_value = 'command:u' THEN 'command_center:u'
                    WHEN permission_value = 'command:d' THEN 'command_center:d'
                    WHEN permission_value LIKE 'command:%' THEN 'command_center:' || substring(permission_value FROM 9)
                    ELSE permission_value
                END AS mapped_permission
                FROM jsonb_array_elements_text(COALESCE("role"."permissions", '[]'::jsonb)) AS permission_values(permission_value)
            ) AS mapped_permissions
        )
        WHERE "permissions" IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(COALESCE("role"."permissions", '[]'::jsonb)) AS permission_values(permission_value)
              WHERE permission_value = 'command:c'
                 OR permission_value = 'command:r'
                 OR permission_value = 'command:u'
                 OR permission_value = 'command:d'
                 OR permission_value LIKE 'command:%'
          );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "permissions_menu" WHERE "model" = 'command_center';
    """
