from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET "Permissions" = '[
            {"label": "新增指挥中心权限", "value": "c"},
            {"label": "删除指挥中心权限", "value": "d"},
            {"label": "修改指挥中心权限", "value": "u"},
            {"label": "查看指挥中心权限", "value": "r"}
        ]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'command_center';

        UPDATE "permissions_menu"
        SET "Permissions" = '[
            {"label": "新增数字员工权限", "value": "c"},
            {"label": "删除数字员工权限", "value": "d"},
            {"label": "修改数字员工权限", "value": "u"},
            {"label": "查看数字员工权限", "value": "r"}
        ]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'digital_employee';

        UPDATE "permissions_menu"
        SET "Permissions" = '[
            {"label": "新增汇报任务权限", "value": "c"},
            {"label": "删除汇报任务权限", "value": "d"},
            {"label": "修改汇报任务权限", "value": "u"},
            {"label": "查看汇报任务权限", "value": "r"}
        ]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'agent_report_schedule';

        UPDATE "role"
        SET "permissions" = (
            SELECT COALESCE(jsonb_agg(DISTINCT mapped_permission), '[]'::jsonb)
            FROM (
                SELECT CASE
                    WHEN permission_value LIKE 'agent:report:schedule:%'
                    THEN 'agent_report_schedule:' || substring(permission_value FROM 23)
                    ELSE permission_value
                END AS mapped_permission
                FROM jsonb_array_elements_text(COALESCE("role"."permissions", '[]'::jsonb)) AS permission_values(permission_value)
            ) AS mapped_permissions
        )
        WHERE "permissions" IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(COALESCE("role"."permissions", '[]'::jsonb)) AS permission_values(permission_value)
              WHERE permission_value LIKE 'agent:report:schedule:%'
          );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET "Permissions" = '[
            "command_center:c",
            "command_center:r",
            "command_center:u",
            "command_center:d"
        ]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'command_center';

        UPDATE "permissions_menu"
        SET "Permissions" = '[
            "digital_employee:c",
            "digital_employee:r",
            "digital_employee:u",
            "digital_employee:d"
        ]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'digital_employee';

        UPDATE "permissions_menu"
        SET "Permissions" = '[
            "agent:report:schedule:c",
            "agent:report:schedule:r",
            "agent:report:schedule:u",
            "agent:report:schedule:d"
        ]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'agent_report_schedule';
    """
