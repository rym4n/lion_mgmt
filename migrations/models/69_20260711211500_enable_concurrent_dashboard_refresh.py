from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE MATERIALIZED VIEW IF NOT EXISTS public.events_day AS
        SELECT (ctime::date) AS day, COUNT(id) AS total, tenant_id
        FROM public.events
        GROUP BY tenant_id, day;

        CREATE MATERIALIZED VIEW IF NOT EXISTS public.events_status AS
        SELECT
            (ctime::date) AS day,
            event_process_status,
            COUNT(id) AS total,
            tenant_id
        FROM public.events
        GROUP BY tenant_id, day, event_process_status;

        CREATE UNIQUE INDEX idx_events_day_refresh_unique
        ON public.events_day (tenant_id, day);

        CREATE UNIQUE INDEX idx_events_status_refresh_unique
        ON public.events_status (tenant_id, day, event_process_status);

        CREATE UNIQUE INDEX idx_runlog_playbook_refresh_unique
        ON public.runlog_playbook (tenant_id, day, playbook_name);

        CREATE UNIQUE INDEX idx_runlog_action_refresh_unique
        ON public.runlog_action (tenant_id, day, action_display_name);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS public.idx_runlog_action_refresh_unique;
        DROP INDEX IF EXISTS public.idx_runlog_playbook_refresh_unique;
        DROP INDEX IF EXISTS public.idx_events_status_refresh_unique;
        DROP INDEX IF EXISTS public.idx_events_day_refresh_unique;
    """
