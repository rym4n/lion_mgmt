from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET "Permissions" = '[
            {"label": "新增数字员工权限", "value": "c"},
            {"label": "删除数字员工权限", "value": "d"},
            {"label": "修改数字员工权限", "value": "u"},
            {"label": "查看数字员工权限", "value": "r"}
        ]'::jsonb
        WHERE "model" = 'digital_employee';

        UPDATE "role"
        SET "permissions" = (
            SELECT COALESCE(jsonb_agg(DISTINCT mapped_permission), '[]'::jsonb)
            FROM (
                SELECT CASE permission_value
                    WHEN 'digital_employee:role:c' THEN 'digital_employee:c'
                    WHEN 'digital_employee:skill:c' THEN 'digital_employee:c'
                    WHEN 'digital_employee:package:import' THEN 'digital_employee:c'
                    WHEN 'digital_employee:role:r' THEN 'digital_employee:r'
                    WHEN 'digital_employee:skill:r' THEN 'digital_employee:r'
                    WHEN 'digital_employee:report:r' THEN 'digital_employee:r'
                    WHEN 'digital_employee:package:export' THEN 'digital_employee:r'
                    WHEN 'digital_employee:package:r' THEN 'digital_employee:r'
                    WHEN 'digital_employee:role:u' THEN 'digital_employee:u'
                    WHEN 'digital_employee:skill:u' THEN 'digital_employee:u'
                    WHEN 'digital_employee:report:u' THEN 'digital_employee:u'
                    WHEN 'digital_employee:role:d' THEN 'digital_employee:d'
                    WHEN 'digital_employee:skill:d' THEN 'digital_employee:d'
                    WHEN 'digital_employee:package:d' THEN 'digital_employee:d'
                    ELSE permission_value
                END AS mapped_permission
                FROM jsonb_array_elements_text(COALESCE("role"."permissions", '[]'::jsonb)) AS permission_values(permission_value)
            ) AS mapped_permissions
        )
        WHERE "permissions" IS NOT NULL
          AND "permissions" ?| ARRAY[
            'digital_employee:role:c',
            'digital_employee:role:r',
            'digital_employee:role:u',
            'digital_employee:role:d',
            'digital_employee:skill:c',
            'digital_employee:skill:r',
            'digital_employee:skill:u',
            'digital_employee:skill:d',
            'digital_employee:report:r',
            'digital_employee:report:u',
            'digital_employee:package:import',
            'digital_employee:package:export',
            'digital_employee:package:r',
            'digital_employee:package:d'
          ];"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET "Permissions" = '[
            "digital_employee:c",
            "digital_employee:r",
            "digital_employee:u",
            "digital_employee:d",
            "digital_employee:role:c",
            "digital_employee:role:r",
            "digital_employee:role:u",
            "digital_employee:role:d",
            "digital_employee:skill:c",
            "digital_employee:skill:r",
            "digital_employee:skill:u",
            "digital_employee:skill:d",
            "digital_employee:report:r",
            "digital_employee:report:u",
            "digital_employee:package:import",
            "digital_employee:package:export",
            "digital_employee:package:r",
            "digital_employee:package:d"
        ]'::jsonb
        WHERE "model" = 'digital_employee';"""


MODELS_STATE = (
    "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"
    "Sp4Ac="
)
