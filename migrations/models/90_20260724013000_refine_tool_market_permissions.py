from tortoise import BaseDBAsyncClient


RUN_IN_TRANSACTION = True


TOOL_MARKET_PERMISSIONS = """[
    {"label": "查看页面", "value": "r"},
    {"label": "资源健康报告", "value": "tool:health:r"},
    {"label": "上传应用", "value": "tool:upload:c"},
    {"label": "删除应用", "value": "tool:app:d"},
    {"label": "新建资源", "value": "tool:resource:c"},
    {"label": "编辑资源", "value": "tool:resource:u"},
    {"label": "应用开发调试", "value": "tool:debug:r"},
    {"label": "删除资源", "value": "tool:resource:d"},
    {"label": "引擎修改", "value": "tool:engine:u"}
]"""

TOOL_PERMISSION_BACKFILLS = [
    ("c", "tool:r"),
    ("c", "tool:upload:c"),
    ("c", "tool:resource:c"),
    ("r", "tool:r"),
    ("r", "tool:health:r"),
    ("r", "tool:debug:r"),
    ("u", "tool:r"),
    ("u", "tool:health:r"),
    ("u", "tool:resource:u"),
    ("u", "tool:engine:u"),
    ("d", "tool:r"),
    ("d", "tool:app:d"),
    ("d", "tool:resource:d"),
]

BACKFILL_VALUES_SQL = ",\n".join(
    f"('{legacy_action}', '{new_permission}')"
    for legacy_action, new_permission in TOOL_PERMISSION_BACKFILLS
)


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        UPDATE "permissions_menu"
        SET
            "description" = '应用管理',
            "Permissions" = '{TOOL_MARKET_PERMISSIONS}'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'tool';

        UPDATE "role" AS r
        SET
            "permissions" = (
                SELECT COALESCE(jsonb_agg(DISTINCT permission_value ORDER BY permission_value), '[]'::jsonb)
                FROM (
                    SELECT permission_value
                    FROM jsonb_array_elements_text(COALESCE(r."permissions", '[]'::jsonb)) AS existing(permission_value)
                    UNION
                    SELECT backfill.new_permission AS permission_value
                    FROM jsonb_array_elements_text(COALESCE(r."permissions", '[]'::jsonb)) AS legacy(permission_value)
                    CROSS JOIN LATERAL (
                        SELECT split_part(legacy.permission_value, ':', 2) AS action_text
                    ) AS parsed
                    CROSS JOIN (VALUES
                        {BACKFILL_VALUES_SQL}
                    ) AS backfill(legacy_action, new_permission)
                    WHERE legacy.permission_value ~ '^tool:(\\*|[cdur](,[cdur])*)$'
                      AND (
                          parsed.action_text = '*'
                          OR backfill.legacy_action = ANY(string_to_array(parsed.action_text, ','))
                      )
                ) AS merged
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE r."permissions" IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(COALESCE(r."permissions", '[]'::jsonb)) AS legacy(permission_value)
              WHERE legacy.permission_value ~ '^tool:(\\*|[cdur](,[cdur])*)$'
          );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET
            "description" = '自定义应用',
            "Permissions" = '[
                {"label": "新增应用权限", "value": "c"},
                {"label": "删除应用权限", "value": "d"},
                {"label": "修改应用权限", "value": "u"},
                {"label": "查看应用权限", "value": "r"}
            ]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'tool';
    """
