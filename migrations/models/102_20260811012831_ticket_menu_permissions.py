from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True

TICKET_PERMISSIONS = '["ticket:c","ticket:r","ticket:u","ticket:all"]'
TICKET_FLOW_PERMISSIONS = '["ticket_flow:r","ticket_flow:u","ticket_flow:d"]'

TICKET_URL = '{"CN":{"meta":{"icon":"ri:file-list-3-line","rank":60,"title":"工单管理"},"path":"/ticket","redirect":"/ticket/list","component":"layout/index.vue","children":[{"meta":{"auths":["ticket:r"],"icon":"ri:list-check-2","permissions":["ticket:r"],"title":"工单列表"},"name":"TicketList","path":"/ticket/list","component":"views/ticket/list/index.vue"}]},"TW":{"meta":{"icon":"ri:file-list-3-line","rank":60,"title":"工單管理"},"path":"/ticket","redirect":"/ticket/list","component":"layout/index.vue","children":[{"meta":{"auths":["ticket:r"],"icon":"ri:list-check-2","permissions":["ticket:r"],"title":"工單列表"},"name":"TicketList","path":"/ticket/list","component":"views/ticket/list/index.vue"}]},"HK":{"meta":{"icon":"ri:file-list-3-line","rank":60,"title":"工單管理"},"path":"/ticket","redirect":"/ticket/list","component":"layout/index.vue","children":[{"meta":{"auths":["ticket:r"],"icon":"ri:list-check-2","permissions":["ticket:r"],"title":"工單列表"},"name":"TicketList","path":"/ticket/list","component":"views/ticket/list/index.vue"}]},"EN":{"meta":{"icon":"ri:file-list-3-line","rank":60,"title":"Ticket Management"},"path":"/ticket","redirect":"/ticket/list","component":"layout/index.vue","children":[{"meta":{"auths":["ticket:r"],"icon":"ri:list-check-2","permissions":["ticket:r"],"title":"Tickets"},"name":"TicketList","path":"/ticket/list","component":"views/ticket/list/index.vue"}]}}'
TICKET_FLOW_URL = '{"CN":{"meta":{"icon":"ri:file-list-3-line","rank":60,"title":"工单管理"},"path":"/ticket","redirect":"/ticket/list","component":"layout/index.vue","children":[{"meta":{"auths":["ticket_flow:r"],"icon":"ri:flow-chart","permissions":["ticket_flow:r"],"title":"流程设计"},"name":"TicketFlow","path":"/ticket/flow","component":"views/ticket/flow/index.vue"}]},"TW":{"meta":{"icon":"ri:file-list-3-line","rank":60,"title":"工單管理"},"path":"/ticket","redirect":"/ticket/list","component":"layout/index.vue","children":[{"meta":{"auths":["ticket_flow:r"],"icon":"ri:flow-chart","permissions":["ticket_flow:r"],"title":"流程設計"},"name":"TicketFlow","path":"/ticket/flow","component":"views/ticket/flow/index.vue"}]},"HK":{"meta":{"icon":"ri:file-list-3-line","rank":60,"title":"工單管理"},"path":"/ticket","redirect":"/ticket/list","component":"layout/index.vue","children":[{"meta":{"auths":["ticket_flow:r"],"icon":"ri:flow-chart","permissions":["ticket_flow:r"],"title":"流程設計"},"name":"TicketFlow","path":"/ticket/flow","component":"views/ticket/flow/index.vue"}]},"EN":{"meta":{"icon":"ri:file-list-3-line","rank":60,"title":"Ticket Management"},"path":"/ticket","redirect":"/ticket/list","component":"layout/index.vue","children":[{"meta":{"auths":["ticket_flow:r"],"icon":"ri:flow-chart","permissions":["ticket_flow:r"],"title":"Flow Design"},"name":"TicketFlow","path":"/ticket/flow","component":"views/ticket/flow/index.vue"}]}}'


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
            item_id="ticket-permissions",
            model="ticket",
            description="工单列表",
            permissions=TICKET_PERMISSIONS,
            url=TICKET_URL,
            bp="ticket_bp",
        )}

        {_insert_menu_sql(
            item_id="ticket-flow-permissions",
            model="ticket_flow",
            description="流程设计",
            permissions=TICKET_FLOW_PERMISSIONS,
            url=TICKET_FLOW_URL,
            bp="ticket_bp",
        )}
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DELETE FROM "permissions_menu"
        WHERE "model" IN ('ticket', 'ticket_flow');
    """
