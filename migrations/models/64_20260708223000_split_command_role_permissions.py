from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

COMMAND_ROLE_PERMISSION_BACKFILLS = [
    ("command_center:c,d,u,r", "command_center:c"),
    ("command_center:c,d,u,r", "command_center:r"),
    ("command_center:c,d,u,r", "command_center:u"),
    ("command_center:c,d,u,r", "command_center:d"),
    ("command_center:c,r,u,d", "command_center:c"),
    ("command_center:c,r,u,d", "command_center:r"),
    ("command_center:c,r,u,d", "command_center:u"),
    ("command_center:c,r,u,d", "command_center:d"),
    ("digital_employee:c,d,u,r", "digital_employee:c"),
    ("digital_employee:c,d,u,r", "digital_employee:r"),
    ("digital_employee:c,d,u,r", "digital_employee:u"),
    ("digital_employee:c,d,u,r", "digital_employee:d"),
    ("digital_employee:c,r,u,d", "digital_employee:c"),
    ("digital_employee:c,r,u,d", "digital_employee:r"),
    ("digital_employee:c,r,u,d", "digital_employee:u"),
    ("digital_employee:c,r,u,d", "digital_employee:d"),
    ("agent:c,d,u,r", "agent:c"),
    ("agent:c,d,u,r", "agent:r"),
    ("agent:c,d,u,r", "agent:u"),
    ("agent:c,d,u,r", "agent:d"),
    ("agent:c,r,u,d", "agent:c"),
    ("agent:c,r,u,d", "agent:r"),
    ("agent:c,r,u,d", "agent:u"),
    ("agent:c,r,u,d", "agent:d"),
    ("agent_report_schedule:c,d,u,r", "agent_report_schedule:c"),
    ("agent_report_schedule:c,d,u,r", "agent_report_schedule:r"),
    ("agent_report_schedule:c,d,u,r", "agent_report_schedule:u"),
    ("agent_report_schedule:c,d,u,r", "agent_report_schedule:d"),
    ("agent_report_schedule:c,r,u,d", "agent_report_schedule:c"),
    ("agent_report_schedule:c,r,u,d", "agent_report_schedule:r"),
    ("agent_report_schedule:c,r,u,d", "agent_report_schedule:u"),
    ("agent_report_schedule:c,r,u,d", "agent_report_schedule:d"),
    ("agent:report:schedule:c,d,u,r", "agent_report_schedule:c"),
    ("agent:report:schedule:c,d,u,r", "agent_report_schedule:r"),
    ("agent:report:schedule:c,d,u,r", "agent_report_schedule:u"),
    ("agent:report:schedule:c,d,u,r", "agent_report_schedule:d"),
    ("agent:report:schedule:c,r,u,d", "agent_report_schedule:c"),
    ("agent:report:schedule:c,r,u,d", "agent_report_schedule:r"),
    ("agent:report:schedule:c,r,u,d", "agent_report_schedule:u"),
    ("agent:report:schedule:c,r,u,d", "agent_report_schedule:d"),
]

BACKFILL_VALUES_SQL = ",\n".join(
    f"('{aggregate_permission}', '{split_permission}')"
    for aggregate_permission, split_permission in COMMAND_ROLE_PERMISSION_BACKFILLS
)


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        UPDATE "role" AS r
        SET
            "permissions" = (
                SELECT COALESCE(jsonb_agg(DISTINCT permission_value ORDER BY permission_value), '[]'::jsonb)
                FROM (
                    SELECT permission_value
                    FROM jsonb_array_elements_text(COALESCE(r."permissions", '[]'::jsonb)) AS existing(permission_value)
                    UNION
                    SELECT backfill.split_permission AS permission_value
                    FROM (VALUES
                        {BACKFILL_VALUES_SQL}
                    ) AS backfill(aggregate_permission, split_permission)
                    WHERE r."permissions" ? backfill.aggregate_permission
                ) AS merged
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE r."permissions" IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM (VALUES
                  {BACKFILL_VALUES_SQL}
              ) AS backfill(aggregate_permission, split_permission)
              WHERE r."permissions" ? backfill.aggregate_permission
          );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        SELECT 1;
    """
