from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

COMMAND_CENTER_PERMISSIONS = '["command_center:c","command_center:r","command_center:u","command_center:d"]'
DIGITAL_EMPLOYEE_PERMISSIONS = '["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"]'
AGENT_PERMISSIONS = '["agent:c","agent:r","agent:u","agent:d"]'
AGENT_REPORT_SCHEDULE_PERMISSIONS = '["agent_report_schedule:c","agent_report_schedule:r","agent_report_schedule:u","agent_report_schedule:d"]'

COMMAND_BOARD_URL = '{"CN":{"meta":{"icon":"ri:command-line","rank":15,"title":"专家团队"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["command_center:c","command_center:r","command_center:u","command_center:d"],"icon":"ri:dashboard-3-line","permissions":["command_center:c","command_center:r","command_center:u","command_center:d"],"title":"指挥中心"},"name":"CommandBoard","path":"/command-center/board","component":"views/commandCenter/board/index.vue"}]},"TW":{"meta":{"icon":"ri:command-line","rank":15,"title":"專家團隊"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["command_center:c","command_center:r","command_center:u","command_center:d"],"icon":"ri:dashboard-3-line","permissions":["command_center:c","command_center:r","command_center:u","command_center:d"],"title":"指揮中心"},"name":"CommandBoard","path":"/command-center/board","component":"views/commandCenter/board/index.vue"}]},"HK":{"meta":{"icon":"ri:command-line","rank":15,"title":"專家團隊"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["command_center:c","command_center:r","command_center:u","command_center:d"],"icon":"ri:dashboard-3-line","permissions":["command_center:c","command_center:r","command_center:u","command_center:d"],"title":"指揮中心"},"name":"CommandBoard","path":"/command-center/board","component":"views/commandCenter/board/index.vue"}]},"EN":{"meta":{"icon":"ri:command-line","rank":15,"title":"Expert Team"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["command_center:c","command_center:r","command_center:u","command_center:d"],"icon":"ri:dashboard-3-line","permissions":["command_center:c","command_center:r","command_center:u","command_center:d"],"title":"Command Center"},"name":"CommandBoard","path":"/command-center/board","component":"views/commandCenter/board/index.vue"}]}}'
DIGITAL_EMPLOYEES_URL = '{"CN":{"meta":{"icon":"ri:command-line","rank":15,"title":"专家团队"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:robot-2-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"数字伙伴"},"name":"CommandEmployees","path":"/command-center/employees","component":"views/commandCenter/employees/index.vue"},{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:tools-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"技能管理"},"name":"CommandSkills","path":"/command-center/skills","component":"views/commandCenter/skills/index.vue"}]},"TW":{"meta":{"icon":"ri:command-line","rank":15,"title":"專家團隊"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:robot-2-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"數字夥伴"},"name":"CommandEmployees","path":"/command-center/employees","component":"views/commandCenter/employees/index.vue"},{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:tools-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"技能管理"},"name":"CommandSkills","path":"/command-center/skills","component":"views/commandCenter/skills/index.vue"}]},"HK":{"meta":{"icon":"ri:command-line","rank":15,"title":"專家團隊"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:robot-2-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"數字夥伴"},"name":"CommandEmployees","path":"/command-center/employees","component":"views/commandCenter/employees/index.vue"},{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:tools-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"技能管理"},"name":"CommandSkills","path":"/command-center/skills","component":"views/commandCenter/skills/index.vue"}]},"EN":{"meta":{"icon":"ri:command-line","rank":15,"title":"Expert Team"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:robot-2-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"Digital Partners"},"name":"CommandEmployees","path":"/command-center/employees","component":"views/commandCenter/employees/index.vue"},{"meta":{"auths":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"icon":"ri:tools-line","permissions":["digital_employee:c","digital_employee:r","digital_employee:u","digital_employee:d"],"title":"Skill Management"},"name":"CommandSkills","path":"/command-center/skills","component":"views/commandCenter/skills/index.vue"}]}}'
REPORT_ASSISTANT_URL = '{"CN":{"meta":{"icon":"ri:command-line","rank":15,"title":"专家团队"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["agent:r"],"icon":"ri:file-text-line","permissions":["agent:r"],"title":"汇报助手"},"name":"ReportAssistant","path":"/command-center/report-assistant","component":"views/commandCenter/reportAssistant/index.vue"}]},"TW":{"meta":{"icon":"ri:command-line","rank":15,"title":"專家團隊"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["agent:r"],"icon":"ri:file-text-line","permissions":["agent:r"],"title":"匯報助手"},"name":"ReportAssistant","path":"/command-center/report-assistant","component":"views/commandCenter/reportAssistant/index.vue"}]},"HK":{"meta":{"icon":"ri:command-line","rank":15,"title":"專家團隊"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["agent:r"],"icon":"ri:file-text-line","permissions":["agent:r"],"title":"匯報助手"},"name":"ReportAssistant","path":"/command-center/report-assistant","component":"views/commandCenter/reportAssistant/index.vue"}]},"EN":{"meta":{"icon":"ri:command-line","rank":15,"title":"Expert Team"},"path":"/command-center","redirect":"/command-center/board","component":"layout/index.vue","children":[{"meta":{"auths":["agent:r"],"icon":"ri:file-text-line","permissions":["agent:r"],"title":"Report Assistant"},"name":"ReportAssistant","path":"/command-center/report-assistant","component":"views/commandCenter/reportAssistant/index.vue"}]}}'


def _insert_menu_sql(
    *,
    item_id: str,
    model: str,
    description: str,
    permissions: str,
    url: str,
    bp: str,
) -> str:
    return f"""
        INSERT INTO "permissions_menu" ("id", "ctime", "utime", "deleted", "tenant_id", "model", "description", "Permissions", "url", "bp")
        VALUES (
            '{item_id}',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            False,
            '8bce552d-c27c-41d4-8a1b-8c5bf38dfe18',
            '{model}',
            '{description}',
            '{permissions}',
            '{url}',
            '{bp}'
        )
        ON CONFLICT ("model") DO UPDATE SET
            "description" = EXCLUDED."description",
            "Permissions" = EXCLUDED."Permissions",
            "url" = EXCLUDED."url",
            "bp" = EXCLUDED."bp",
            "deleted" = False,
            "utime" = CURRENT_TIMESTAMP;
    """


async def upgrade(db: BaseDBAsyncClient) -> str:
    return f"""
        {_insert_menu_sql(
            item_id="command-center-permissions",
            model="command_center",
            description="指挥中心",
            permissions=COMMAND_CENTER_PERMISSIONS,
            url=COMMAND_BOARD_URL,
            bp="command_bp",
        )}

        {_insert_menu_sql(
            item_id="digital-employee-permissions",
            model="digital_employee",
            description="数字伙伴",
            permissions=DIGITAL_EMPLOYEE_PERMISSIONS,
            url=DIGITAL_EMPLOYEES_URL,
            bp="digital_employee_bp",
        )}

        {_insert_menu_sql(
            item_id="agent-permissions",
            model="agent",
            description="汇报助手",
            permissions=AGENT_PERMISSIONS,
            url=REPORT_ASSISTANT_URL,
            bp="agent_bp",
        )}

        UPDATE "permissions_menu"
        SET
            "description" = '汇报助手',
            "Permissions" = '{AGENT_REPORT_SCHEDULE_PERMISSIONS}'::jsonb,
            "bp" = 'agent_bp',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'agent_report_schedule';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "permissions_menu"
        SET
            "description" = '专家团队',
            "url" = '[]'::jsonb,
            "bp" = 'command_center_bp',
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" = 'command_center';

        UPDATE "permissions_menu"
        SET
            "url" = '[]'::jsonb,
            "utime" = CURRENT_TIMESTAMP
        WHERE "model" IN ('digital_employee', 'agent');
    """
