from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

DIGITAL_EMPLOYEE_URL = '{"CN":{"meta":{"icon":"ri:command-line","rank":15,"title":"专家团队"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:robot-2-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"数字员工"},"name":"CommandEmployees","path":"/command-center/employees","component":"views/commandCenter/employees/index.vue"}]},"TW":{"meta":{"icon":"ri:command-line","rank":15,"title":"專家團隊"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:robot-2-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"數字員工"},"name":"CommandEmployees","path":"/command-center/employees","component":"views/commandCenter/employees/index.vue"}]},"HK":{"meta":{"icon":"ri:command-line","rank":15,"title":"專家團隊"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:robot-2-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"數字員工"},"name":"CommandEmployees","path":"/command-center/employees","component":"views/commandCenter/employees/index.vue"}]},"EN":{"meta":{"icon":"ri:command-line","rank":15,"title":"Expert Team"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:robot-2-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"Digital Employees"},"name":"CommandEmployees","path":"/command-center/employees","component":"views/commandCenter/employees/index.vue"}]}}'
DIGITAL_EMPLOYEE_SKILL_PERMISSIONS = '[{"label":"新增技能管理权限","value":"c"},{"label":"删除技能管理权限","value":"d"},{"label":"修改技能管理权限","value":"u"},{"label":"查看技能管理权限","value":"r"}]'
DIGITAL_EMPLOYEE_SKILL_URL = '{"CN":{"meta":{"icon":"ri:command-line","rank":15,"title":"专家团队"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee_skill:c","digital_employee_skill:r","digital_employee_skill:u","digital_employee_skill:d"],"icon":"ri:tools-line","permissions":["digital_employee_skill:c","digital_employee_skill:r","digital_employee_skill:u","digital_employee_skill:d"],"title":"技能管理"},"name":"CommandSkills","path":"/command-center/skills","component":"views/commandCenter/skills/index.vue"}]},"TW":{"meta":{"icon":"ri:command-line","rank":15,"title":"專家團隊"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee_skill:c","digital_employee_skill:r","digital_employee_skill:u","digital_employee_skill:d"],"icon":"ri:tools-line","permissions":["digital_employee_skill:c","digital_employee_skill:r","digital_employee_skill:u","digital_employee_skill:d"],"title":"技能管理"},"name":"CommandSkills","path":"/command-center/skills","component":"views/commandCenter/skills/index.vue"}]},"HK":{"meta":{"icon":"ri:command-line","rank":15,"title":"專家團隊"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee_skill:c","digital_employee_skill:r","digital_employee_skill:u","digital_employee_skill:d"],"icon":"ri:tools-line","permissions":["digital_employee_skill:c","digital_employee_skill:r","digital_employee_skill:u","digital_employee_skill:d"],"title":"技能管理"},"name":"CommandSkills","path":"/command-center/skills","component":"views/commandCenter/skills/index.vue"}]},"EN":{"meta":{"icon":"ri:command-line","rank":15,"title":"Expert Team"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee_skill:c","digital_employee_skill:r","digital_employee_skill:u","digital_employee_skill:d"],"icon":"ri:tools-line","permissions":["digital_employee_skill:c","digital_employee_skill:r","digital_employee_skill:u","digital_employee_skill:d"],"title":"Skill Management"},"name":"CommandSkills","path":"/command-center/skills","component":"views/commandCenter/skills/index.vue"}]}}'
DIGITAL_EMPLOYEE_SKILL_BACKFILLS = [
    ("digital_employee:*", "digital_employee_skill:c"),
    ("digital_employee:*", "digital_employee_skill:r"),
    ("digital_employee:*", "digital_employee_skill:u"),
    ("digital_employee:*", "digital_employee_skill:d"),
    ("digital_employee:c,d,u,r", "digital_employee_skill:c"),
    ("digital_employee:c,d,u,r", "digital_employee_skill:r"),
    ("digital_employee:c,d,u,r", "digital_employee_skill:u"),
    ("digital_employee:c,d,u,r", "digital_employee_skill:d"),
    ("digital_employee:c,r,u,d", "digital_employee_skill:c"),
    ("digital_employee:c,r,u,d", "digital_employee_skill:r"),
    ("digital_employee:c,r,u,d", "digital_employee_skill:u"),
    ("digital_employee:c,r,u,d", "digital_employee_skill:d"),
    ("digital_employee:c", "digital_employee_skill:c"),
    ("digital_employee:r", "digital_employee_skill:r"),
    ("digital_employee:u", "digital_employee_skill:u"),
    ("digital_employee:d", "digital_employee_skill:d"),
]
BACKFILL_VALUES_SQL = ",\n".join(
    f"('{digital_employee_permission}', '{skill_permission}')"
    for digital_employee_permission, skill_permission in DIGITAL_EMPLOYEE_SKILL_BACKFILLS
)


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        UPDATE "permissions_menu"
        SET
            "description" = '数字员工',
            "url" = '{DIGITAL_EMPLOYEE_URL}'::jsonb,
            "bp" = 'digital_employee_bp',
            "deleted" = False,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'digital_employee';

        INSERT INTO "permissions_menu" ("id", "ctime", "utime", "deleted", "tenant_id", "model", "description", "Permissions", "url", "bp")
        VALUES (
            'digital-employee-skill-perm',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            False,
            '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            'digital_employee_skill',
            '技能管理',
            '{DIGITAL_EMPLOYEE_SKILL_PERMISSIONS}'::jsonb,
            '{DIGITAL_EMPLOYEE_SKILL_URL}'::jsonb,
            'digital_employee_bp'
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
                    SELECT backfill.skill_permission AS permission_value
                    FROM (VALUES
                        {BACKFILL_VALUES_SQL}
                    ) AS backfill(digital_employee_permission, skill_permission)
                    WHERE r."permissions" ? backfill.digital_employee_permission
                ) AS merged
            ),
            "utime" = CURRENT_TIMESTAMP
        WHERE r."permissions" IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM (VALUES
                  {BACKFILL_VALUES_SQL}
              ) AS backfill(digital_employee_permission, skill_permission)
              WHERE r."permissions" ? backfill.digital_employee_permission
          );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "permissions_menu"
        WHERE "model" = 'digital_employee_skill';
    """
