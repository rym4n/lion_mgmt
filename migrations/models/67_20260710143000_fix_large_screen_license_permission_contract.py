from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

LARGE_SCREEN_PERMISSIONS = '[{"label":"新增大屏权限","value":"c"},{"label":"删除大屏权限","value":"d"},{"label":"修改大屏权限","value":"u"},{"label":"查看大屏权限","value":"r"}]'
LARGE_SCREEN_URL = '{"CN":{"meta":{"icon":"streamline-plump:screen-1","rank":70,"title":"大屏管理"},"path":"/screen","children":[{"meta":{"permissions":["large_screen:r"],"auths":["large_screen:r"],"title":"大屏管理"},"name":"screen","path":"/screen/index","component":"views/screen/index.vue"}],"redirect":"/screen/index","component":"layout/index.vue"},"EN":{"meta":{"icon":"streamline-plump:screen-1","rank":70,"title":"Screen"},"path":"/screen","children":[{"meta":{"permissions":["large_screen:r"],"auths":["large_screen:r"],"title":"Screen"},"name":"screen","path":"/screen/index","component":"views/screen/index.vue"}],"redirect":"/screen/index","component":"layout/index.vue"},"HK":{"meta":{"icon":"streamline-plump:screen-1","rank":70,"title":"大螢幕管理"},"path":"/screen","children":[{"meta":{"permissions":["large_screen:r"],"auths":["large_screen:r"],"title":"大螢幕管理"},"name":"screen","path":"/screen/index","component":"views/screen/index.vue"}],"redirect":"/screen/index","component":"layout/index.vue"}}'

SCREEN_PERMISSION_BACKFILLS = [
    ("screen:*", "large_screen:c"),
    ("screen:*", "large_screen:r"),
    ("screen:*", "large_screen:u"),
    ("screen:*", "large_screen:d"),
    ("screen:c,d,u,r", "large_screen:c"),
    ("screen:c,d,u,r", "large_screen:r"),
    ("screen:c,d,u,r", "large_screen:u"),
    ("screen:c,d,u,r", "large_screen:d"),
    ("screen:c,r,u,d", "large_screen:c"),
    ("screen:c,r,u,d", "large_screen:r"),
    ("screen:c,r,u,d", "large_screen:u"),
    ("screen:c,r,u,d", "large_screen:d"),
    ("screen:c", "large_screen:c"),
    ("screen:r", "large_screen:r"),
    ("screen:u", "large_screen:u"),
    ("screen:d", "large_screen:d"),
]

BACKFILL_VALUES_SQL = ",\n".join(
    f"('{screen_permission}', '{large_screen_permission}')"
    for screen_permission, large_screen_permission in SCREEN_PERMISSION_BACKFILLS
)


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        DELETE FROM "permissions_menu" AS legacy_screen
        WHERE legacy_screen."model" = 'screen'
          AND legacy_screen."description" = '大屏管理'
          AND EXISTS (
              SELECT 1
              FROM "permissions_menu" AS canonical_screen
              WHERE canonical_screen."model" = 'large_screen'
          );

        UPDATE "permissions_menu"
        SET
            "model" = 'large_screen',
            "description" = '大屏管理',
            "Permissions" = '{LARGE_SCREEN_PERMISSIONS}'::jsonb,
            "url" = '{LARGE_SCREEN_URL}'::jsonb,
            "bp" = 'dashboard_bp',
            "deleted" = False,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'screen'
          AND "description" = '大屏管理';

        INSERT INTO "permissions_menu" ("id", "ctime", "utime", "deleted", "tenant_id", "model", "description", "Permissions", "url", "bp")
        VALUES (
            '7f8c2d6a-9e41-4c3f-b2a7-5d91e8fa4b6c',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            False,
            '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            'large_screen',
            '大屏管理',
            '{LARGE_SCREEN_PERMISSIONS}'::jsonb,
            '{LARGE_SCREEN_URL}'::jsonb,
            'dashboard_bp'
        )
        ON CONFLICT ("model") DO UPDATE SET
            "description" = EXCLUDED."description",
            "Permissions" = EXCLUDED."Permissions",
            "url" = EXCLUDED."url",
            "bp" = EXCLUDED."bp",
            "deleted" = False,
            "utime" = CURRENT_TIMESTAMP;

        UPDATE "role" AS r
        SET
            "permissions" = (
                SELECT COALESCE(jsonb_agg(DISTINCT permission_value ORDER BY permission_value), '[]'::jsonb)
                FROM (
                    SELECT permission_value
                    FROM jsonb_array_elements_text(COALESCE(r."permissions", '[]'::jsonb)) AS existing(permission_value)
                    UNION
                    SELECT 'large_screen:c,d,u,r' AS permission_value
                ) AS merged
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE r."name" = '租户管理员'
          AND NOT (COALESCE(r."permissions", '[]'::jsonb) ? 'large_screen:c,d,u,r');

        UPDATE "role" AS r
        SET
            "permissions" = (
                SELECT COALESCE(jsonb_agg(DISTINCT permission_value ORDER BY permission_value), '[]'::jsonb)
                FROM (
                    SELECT permission_value
                    FROM jsonb_array_elements_text(COALESCE(r."permissions", '[]'::jsonb)) AS existing(permission_value)
                    UNION
                    SELECT backfill.large_screen_permission AS permission_value
                    FROM (VALUES
                        {BACKFILL_VALUES_SQL}
                    ) AS backfill(screen_permission, large_screen_permission)
                    WHERE r."permissions" ? backfill.screen_permission
                ) AS merged
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE r."permissions" IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM (VALUES
                  {BACKFILL_VALUES_SQL}
              ) AS backfill(screen_permission, large_screen_permission)
              WHERE r."permissions" ? backfill.screen_permission
          );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        UPDATE "permissions_menu"
        SET
            "model" = 'screen',
            "Permissions" = '{LARGE_SCREEN_PERMISSIONS}'::jsonb,
            "bp" = 'dashboard_bp',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'large_screen'
          AND "description" = '大屏管理';
    """
