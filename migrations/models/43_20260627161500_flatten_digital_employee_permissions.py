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
